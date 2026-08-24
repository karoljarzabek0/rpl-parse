"""
High-Throughput Multi-Threaded ETL, Embedding, S3 Upload, and SQLite RRF Pipeline for RPL.
Architecture:
  - Producer ThreadPool (32 workers): Concurrent S3 fetching & CPU tokenization into 4096-token chunks (256 overlap)
  - Dedicated GPU Worker: Continuous batched inference with ModernBERT (bfloat16, SDPA, dynamic batching)
  - Consumer ThreadPool (32 workers): Concurrent S3 individual binary upload (s3://plek/embeddings/{id}.bin)
  - SQLite Batch Writer: Atomically indexes vec0 & Morfeusz FTS5 virtual tables
"""

import os
import sys
import time
import json
import queue
import sqlite3
import threading
import argparse
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional, Set, Tuple

import boto3
from botocore.config import Config
import numpy as np
import torch
from transformers import AutoTokenizer
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn, MofNCompleteColumn

console = Console()

MODEL_NAME = "OPI-PIB/PolDense-400M"
CHUNK_SIZE = 4096
CHUNK_OVERLAP = 256
S3_BUCKET = "plek"
S3_MD_PREFIX = "md/"
S3_EMB_PREFIX = "embeddings/"
S3_ENDPOINT = "https://s3.waw.io.cloud.ovh.net"
S3_REGION = "waw"
DEFAULT_DB_PATH = "data/rpl.db"
DEFAULT_ATC_MAP_PATH = "data/atc_map.json"
VEC_EXT_PATH = "extensions/vec0.so"
MORFEUSZ_EXT_PATH = "extensions/morfeusz.so"


def create_s3_client(max_connections: int = 50):
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        config=Config(
            s3={"addressing_style": "path"},
            max_pool_connections=max_connections,
            retries={"max_attempts": 5, "mode": "standard"}
        )
    )


def list_existing_s3_embeddings(s3_client) -> Set[int]:
    console.print(f"[cyan]Scanning existing embeddings in s3://{S3_BUCKET}/{S3_EMB_PREFIX}...[/cyan]")
    existing_ids = set()
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_EMB_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".bin"):
                fname = os.path.basename(key).replace(".bin", "")
                try:
                    existing_ids.add(int(fname))
                except ValueError:
                    pass
    console.print(f"[green]Found {len(existing_ids)} existing embeddings in S3.[/green]")
    return existing_ids


def list_s3_md_files(s3_client, limit: Optional[int] = None) -> List[str]:
    console.print(f"[cyan]Listing markdown files from s3://{S3_BUCKET}/{S3_MD_PREFIX}...[/cyan]")
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_MD_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".md") and key != S3_MD_PREFIX:
                keys.append(key)
                if limit and len(keys) >= limit:
                    return keys
    console.print(f"[green]Found total {len(keys)} markdown files in S3.[/green]")
    return keys


def load_atc_map(atc_map_path: str) -> Dict[str, Any]:
    if os.path.exists(atc_map_path):
        with open(atc_map_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def format_atc_for_fts(atc_codes: List[str], atc_map: Dict[str, Any]) -> str:
    descriptions = []
    for code in atc_codes:
        code_clean = code.strip().upper()
        if not code_clean:
            continue
        g = code_clean[0]
        sg = code_clean[:3] if len(code_clean) >= 3 else ""
        g_name = atc_map.get(g, {}).get("name", "")
        sg_name = atc_map.get(g, {}).get("subgroups", {}).get(sg, "")
        part = f"{code_clean} {sg_name} {g_name}".strip()
        if part:
            descriptions.append(part)
    return " | ".join(descriptions)


def get_metadata_lookup(db_conn: sqlite3.Connection, atc_map: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    console.print("[cyan]Pre-loading medicine metadata from SQLite...[/cyan]")
    c = db_conn.cursor()
    c.execute("SELECT id, nazwa_produktu, nazwa_powszechnie_stosowana FROM produkty_lecznicze")
    products = {row[0]: {"nazwa": row[1], "powszechna": row[2]} for row in c.fetchall()}

    c.execute("SELECT produkt_id, nazwa_substancji FROM substancje_czynne")
    for pid, subst in c.fetchall():
        if pid in products:
            products[pid].setdefault("substancje", []).append(subst)

    c.execute("SELECT produkt_id, kod_atc FROM kody_atc")
    for pid, atc in c.fetchall():
        if pid in products:
            products[pid].setdefault("atc", []).append(atc)

    for pid, p in products.items():
        subst_list = p.get("substancje", [])
        atc_list = p.get("atc", [])
        p["subst_str"] = ", ".join(subst_list)
        p["atc_str"] = format_atc_for_fts(atc_list, atc_map)

    console.print(f"[green]Loaded metadata for {len(products)} products.[/green]")
    return products


def init_database_tables(db_conn: sqlite3.Connection):
    db_conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS vec_dokumenty USING vec0(
        produkt_id INTEGER PRIMARY KEY,
        embedding float[1024] distance_metric=cosine
    );
    """)
    db_conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS fts_dokumenty USING fts5(
        produkt_id UNINDEXED,
        nazwa_produktu,
        nazwa_powszechnie_stosowana,
        substancje_czynne,
        klasyfikacja_atc,
        chpl_content,
        tokenize='morfeusz'
    );
    """)
    db_conn.commit()


def split_text_into_chunks(text: str, tokenizer: AutoTokenizer, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    if not text.strip():
        return [], 0

    encoded = tokenizer(text, add_special_tokens=False)
    input_ids = encoded["input_ids"]
    total_tokens = len(input_ids)
    if total_tokens == 0:
        return [], 0

    stride = chunk_size - overlap
    chunks_tokens = []

    if total_tokens <= chunk_size:
        chunks_tokens.append(input_ids)
    else:
        for i in range(0, total_tokens, stride):
            chunk = input_ids[i : i + chunk_size]
            chunks_tokens.append(chunk)
            if i + chunk_size >= total_tokens:
                break

    decoded_chunks = [
        tokenizer.decode(c, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        for c in chunks_tokens
    ]
    return decoded_chunks, total_tokens


def main():
    parser = argparse.ArgumentParser(description="High-Throughput RPL Embedding & RRF Loader")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of documents (None for all ~13.3k)")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    parser.add_argument("--atc-map", type=str, default=DEFAULT_ATC_MAP_PATH, help="Path to atc_map.json")
    parser.add_argument("--batch-size", type=int, default=8, help="GPU inference batch size")
    parser.add_argument("--io-workers", type=int, default=32, help="Number of concurrent S3 I/O threads")
    parser.add_argument("--skip-s3-upload", action="store_true", help="Skip uploading individual embeddings to S3")
    args = parser.parse_args()

    console.rule("[bold green]High-Throughput RPL Embedding & RRF Indexing Pipeline[/bold green]")

    # CUDA optimizations
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    device = "cuda" if torch.cuda.is_available() else "cpu"
    console.print(f"• Device: [bold green]{device.upper()}[/bold green]" + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else ""))
    console.print(f"• Model: [bold yellow]{MODEL_NAME}[/bold yellow] (1024-d, 4096 context, 256 overlap)")
    console.print(f"• Target Database: [bold yellow]{args.db}[/bold yellow]")
    console.print(f"• S3 Destination: [bold yellow]s3://{S3_BUCKET}/{S3_EMB_PREFIX}[/bold yellow]")
    console.print(f"• Concurrent I/O Threads: [bold yellow]{args.io_workers}[/bold yellow]\n")

    # 1. S3 and Database connection
    s3_client = create_s3_client(max_connections=args.io_workers * 2)
    md_keys = list_s3_md_files(s3_client, limit=args.limit)

    db_conn = sqlite3.connect(args.db, check_same_thread=False)
    db_conn.enable_load_extension(True)
    db_conn.load_extension(VEC_EXT_PATH)
    db_conn.load_extension(MORFEUSZ_EXT_PATH)
    db_conn.enable_load_extension(False)

    init_database_tables(db_conn)
    atc_map = load_atc_map(args.atc_map)
    metadata_lookup = get_metadata_lookup(db_conn, atc_map)

    # 2. Check existing embeddings to allow instant resuming
    existing_s3_ids = list_existing_s3_embeddings(s3_client) if not args.skip_s3_upload else set()
    c = db_conn.cursor()
    c.execute("SELECT produkt_id FROM vec_dokumenty")
    existing_db_ids = set(row[0] for row in c.fetchall())

    # Filter keys to only unprocessed ones
    keys_to_process = []
    for key in md_keys:
        fname = os.path.basename(key).replace(".md", "")
        try:
            prod_id = int(fname)
        except ValueError:
            prod_id = 0

        # Skip only if already present in both DB and S3
        if prod_id in existing_db_ids and (args.skip_s3_upload or prod_id in existing_s3_ids):
            continue
        keys_to_process.append(key)

    console.print(f"[bold green]{len(md_keys) - len(keys_to_process)} already processed. {len(keys_to_process)} remaining to process.[/bold green]\n")

    if not keys_to_process:
        console.print("[green]All requested documents are already fully embedded and indexed![/green]")
        return

    # 3. Load Tokenizer & Model
    console.print("[cyan]Loading Tokenizer & Model...[/cyan]")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.model_max_length = int(1e9)  # Suppress false-positive length warning

    model = SentenceTransformer(
        MODEL_NAME,
        device=device,
        model_kwargs={
            "torch_dtype": torch.bfloat16 if device == "cuda" else torch.float32,
            "attn_implementation": "sdpa"
        }
    )
    console.print("[green]Model loaded and optimized with SDPA bfloat16![/green]\n")

    # 4. Queues for Producer-Consumer Pipeline
    doc_queue = queue.Queue(maxsize=128)
    upload_queue = queue.Queue(maxsize=256)
    stop_event = threading.Event()

    # Producer: Fetches from S3 & chunks on CPU in parallel
    def s3_fetch_and_chunk_worker(key: str):
        fname = os.path.basename(key).replace(".md", "")
        try:
            prod_id = int(fname)
        except ValueError:
            prod_id = 0

        thread_s3 = create_s3_client(max_connections=5)
        try:
            resp = thread_s3.get_object(Bucket=S3_BUCKET, Key=key)
            doc_text = resp["Body"].read().decode("utf-8", errors="replace")
            chunks, tok_count = split_text_into_chunks(doc_text, tokenizer, CHUNK_SIZE, CHUNK_OVERLAP)
            return (prod_id, key, doc_text, chunks, tok_count)
        except Exception as e:
            console.print(f"[red]Error downloading {key}: {e}[/red]")
            return None

    def producer_thread():
        with ThreadPoolExecutor(max_workers=args.io_workers) as executor:
            futures = [executor.submit(s3_fetch_and_chunk_worker, k) for k in keys_to_process]
            for f in futures:
                if stop_event.is_set():
                    break
                res = f.result()
                if res is not None:
                    doc_queue.put(res)
        doc_queue.put(None)  # Sentinel

    # Uploader: S3 uploads in parallel
    def uploader_worker():
        thread_s3 = create_s3_client(max_connections=5)
        while not stop_event.is_set():
            try:
                item = upload_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:
                upload_queue.task_done()
                break

            prod_id, vec_bytes = item
            if not args.skip_s3_upload and prod_id not in existing_s3_ids:
                try:
                    s3_emb_key = f"{S3_EMB_PREFIX}{prod_id}.bin"
                    thread_s3.put_object(
                        Bucket=S3_BUCKET,
                        Key=s3_emb_key,
                        Body=vec_bytes,
                        ContentType="application/octet-stream"
                    )
                except Exception as e:
                    console.print(f"[red]Error uploading {prod_id}.bin to S3: {e}[/red]")

            upload_queue.task_done()

    # Start Producer Thread
    t_producer = threading.Thread(target=producer_thread, daemon=True)
    t_producer.start()

    # Start Uploader Threads
    upload_threads = []
    for _ in range(min(args.io_workers, 16)):
        t_up = threading.Thread(target=uploader_worker, daemon=True)
        t_up.start()
        upload_threads.append(t_up)

    start_time = time.time()
    processed_count = 0
    total_tokens_count = 0
    total_chunks_count = 0

    all_ids = []
    all_embeddings = []

    # Main GPU Inference & DB Writer Loop
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Embedding & Indexing documents...", total=len(keys_to_process))

        batch_docs = []
        batch_ids = []
        batch_chunks = []

        def flush_gpu_batch():
            nonlocal processed_count, total_tokens_count, total_chunks_count
            if not batch_docs:
                return

            flat_chunks = []
            chunk_doc_indices = []
            for doc_idx, chunks in enumerate(batch_chunks):
                for chunk in chunks:
                    flat_chunks.append(chunk)
                    chunk_doc_indices.append(doc_idx)

            if flat_chunks:
                with torch.inference_mode():
                    flat_vectors = model.encode(
                        flat_chunks,
                        batch_size=args.batch_size,
                        convert_to_numpy=True,
                        show_progress_bar=False,
                        normalize_embeddings=False
                    )

                # Group chunk vectors by document and compute mean
                doc_vec_lists = [[] for _ in range(len(batch_docs))]
                for vec, doc_idx in zip(flat_vectors, chunk_doc_indices):
                    doc_vec_lists[doc_idx].append(vec)

                for prod_id, doc_text, vec_list in zip(batch_ids, batch_docs, doc_vec_lists):
                    if vec_list:
                        mean_vec = np.mean(vec_list, axis=0)
                    else:
                        mean_vec = np.zeros(1024, dtype=np.float32)

                    norm = np.linalg.norm(mean_vec)
                    if norm > 1e-12:
                        mean_vec = mean_vec / norm
                    mean_vec = mean_vec.astype(np.float32)
                    vec_bytes = mean_vec.tobytes()

                    # SQLite atomic write
                    db_conn.execute("DELETE FROM vec_dokumenty WHERE produkt_id = ?", (prod_id,))
                    db_conn.execute("INSERT INTO vec_dokumenty(produkt_id, embedding) VALUES (?, ?)", (prod_id, vec_bytes))

                    meta = metadata_lookup.get(prod_id, {})
                    db_conn.execute("DELETE FROM fts_dokumenty WHERE produkt_id = ?", (str(prod_id),))
                    db_conn.execute("""
                    INSERT INTO fts_dokumenty(
                        produkt_id, nazwa_produktu, nazwa_powszechnie_stosowana,
                        substancje_czynne, klasyfikacja_atc, chpl_content
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        str(prod_id),
                        meta.get("nazwa", ""),
                        meta.get("powszechna", ""),
                        meta.get("subst_str", ""),
                        meta.get("atc_str", ""),
                        doc_text
                    ))

                    # Queue S3 upload
                    upload_queue.put((prod_id, vec_bytes))

                    all_ids.append(prod_id)
                    all_embeddings.append(mean_vec)
                    processed_count += 1
                    progress.advance(task)

                db_conn.commit()

            batch_docs.clear()
            batch_ids.clear()
            batch_chunks.clear()

        while True:
            item = doc_queue.get()
            if item is None:
                flush_gpu_batch()
                break

            prod_id, key, doc_text, chunks, tok_count = item
            total_tokens_count += tok_count
            total_chunks_count += len(chunks)

            batch_docs.append(doc_text)
            batch_ids.append(prod_id)
            batch_chunks.append(chunks)

            # Flush when batch reaches optimal GPU batch capacity
            num_chunks_in_batch = sum(len(c) for c in batch_chunks)
            if num_chunks_in_batch >= args.batch_size * 2 or len(batch_docs) >= 16:
                flush_gpu_batch()

        # Flush any remainder
        flush_gpu_batch()

    # Stop uploader threads
    for _ in upload_threads:
        upload_queue.put(None)
    for t_up in upload_threads:
        t_up.join()

    # Upload consolidated archive
    if all_embeddings:
        npz_path = "data/embeddings.npz"
        console.print(f"[cyan]Updating consolidated archive at {npz_path}...[/cyan]")
        np.savez_compressed(
            npz_path,
            ids=np.array(all_ids, dtype=np.int64),
            embeddings=np.array(all_embeddings, dtype=np.float32)
        )
        if not args.skip_s3_upload:
            console.print(f"[cyan]Uploading consolidated archive to s3://{S3_BUCKET}/{S3_EMB_PREFIX}embeddings.npz...[/cyan]")
            with open(npz_path, "rb") as f:
                s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=f"{S3_EMB_PREFIX}embeddings.npz",
                    Body=f.read()
                )
            console.print("[green]Consolidated archive uploaded to S3![/green]")

    elapsed = time.time() - start_time
    total_vec_rows = db_conn.execute("SELECT count(*) FROM vec_dokumenty").fetchone()[0]
    total_fts_rows = db_conn.execute("SELECT count(*) FROM fts_dokumenty").fetchone()[0]
    db_conn.close()

    console.print("\n[bold green]High-Throughput Pipeline Complete![/bold green]")
    table = Table(title="Pipeline Performance Summary", show_lines=True)
    table.add_column("Metric", style="cyan bold")
    table.add_column("Value", style="green")

    table.add_row("Documents Processed in this run", str(processed_count))
    table.add_row("Total Rows in vec_dokumenty (vec0)", str(total_vec_rows))
    table.add_row("Total Rows in fts_dokumenty (Morfeusz FTS5)", str(total_fts_rows))
    table.add_row("Total Tokens Encoded", f"{total_tokens_count:,}")
    table.add_row("Duration", f"{elapsed:.2f}s ([bold]{processed_count / max(elapsed, 0.001):.1f} docs/sec[/bold])")
    table.add_row("SQLite Unified Database", os.path.abspath(args.db))
    console.print(table)


if __name__ == "__main__":
    main()
