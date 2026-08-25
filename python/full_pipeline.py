"""
Full-scale ETL, Embedding, S3 Upload, and Database Indexing Pipeline for RPL.
Processes all ~13,337 medicinal ChPL documents:
  - Fetches ChPL Markdown files from S3 (`s3://plek/md/`)
  - Chunks into 4096-token windows with 256-token overlap (PolDense-400M tokenizer)
  - Computes 1024-d mean document embeddings on CUDA GPU
  - Uploads individual embeddings to S3 (`s3://plek/embeddings/{id}.bin`)
  - Indexes into SQLite:
      1. sqlite-vec `vec_dokumenty`
      2. FTS5 with Morfeusz lemmatizer `fts_dokumenty`
      3. Relational XML tables `produkty_lecznicze`
  - Saves consolidated NPZ archive (`data/embeddings.npz`) and uploads to S3
"""

import os
import sys
import time
import json
import sqlite3
import argparse
from typing import List, Dict, Any, Optional, Set

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


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        config=Config(
            s3={"addressing_style": "path"},
            max_pool_connections=50,
            retries={"max_attempts": 5, "mode": "standard"}
        )
    )


def list_existing_s3_embeddings(s3_client) -> Set[int]:
    """Lists already uploaded embedding IDs in S3 to allow resuming seamlessly."""
    console.print(f"[cyan]Checking existing embeddings in s3://{S3_BUCKET}/{S3_EMB_PREFIX}...[/cyan]")
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
    """Lists markdown file keys from S3."""
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


def main():
    parser = argparse.ArgumentParser(description="Full RPL Document Embedding & SQLite RRF Loader")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of documents (None for all ~13.3k)")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    parser.add_argument("--atc-map", type=str, default=DEFAULT_ATC_MAP_PATH, help="Path to atc_map.json")
    parser.add_argument("--batch-size", type=int, default=16, help="GPU inference batch size")
    parser.add_argument("--skip-s3-upload", action="store_true", help="Skip uploading individual embeddings to S3")
    args = parser.parse_args()

    console.rule("[bold green]RPL Full-Scale Embedding & RRF Indexing Pipeline[/bold green]")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    console.print(f"• Device: [bold green]{device.upper()}[/bold green]" + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else ""))
    console.print(f"• Model: [bold yellow]{MODEL_NAME}[/bold yellow] (1024-d, 4096 context, 256 overlap)")
    console.print(f"• Target Database: [bold yellow]{args.db}[/bold yellow]")
    console.print(f"• S3 Destination: [bold yellow]s3://{S3_BUCKET}/{S3_EMB_PREFIX}[/bold yellow]\n")

    # 1. Connect to S3 & Database
    s3_client = get_s3_client()
    md_keys = list_s3_md_files(s3_client, limit=args.limit)

    db_conn = sqlite3.connect(args.db)
    db_conn.enable_load_extension(True)
    db_conn.load_extension(VEC_EXT_PATH)
    db_conn.load_extension(MORFEUSZ_EXT_PATH)
    db_conn.enable_load_extension(False)

    init_database_tables(db_conn)
    atc_map = load_atc_map(args.atc_map)
    metadata_lookup = get_metadata_lookup(db_conn, atc_map)

    # 2. Check existing embeddings in S3 and in SQLite vec_dokumenty
    existing_s3_ids = list_existing_s3_embeddings(s3_client) if not args.skip_s3_upload else set()
    c = db_conn.cursor()
    c.execute("SELECT produkt_id FROM vec_dokumenty")
    existing_db_ids = set(row[0] for row in c.fetchall())

    # 3. Load Tokenizer and SentenceTransformer
    console.print("[cyan]Loading Tokenizer & Model...[/cyan]")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = SentenceTransformer(
        MODEL_NAME,
        device=device,
        model_kwargs={
            "torch_dtype": torch.bfloat16 if device == "cuda" else torch.float32,
        }
    )
    console.print("[green]Model ready![/green]\n")

    # 4. Processing Loop
    start_time = time.time()
    processed_count = 0
    uploaded_s3_count = 0
    total_tokens_count = 0
    total_chunks_count = 0

    all_ids = []
    all_embeddings = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Embedding & Indexing documents...", total=len(md_keys))

        batch_docs = []
        batch_ids = []
        batch_keys = []
        batch_chunks = []
        batch_chunk_slices = []

        def flush_batch():
            nonlocal uploaded_s3_count, total_tokens_count, total_chunks_count, processed_count
            if not batch_docs:
                return

            # Flatten chunks for batched model inference
            flat_chunks = []
            for chunks in batch_chunks:
                flat_chunks.extend(chunks)

            if flat_chunks:
                flat_vectors = model.encode(
                    flat_chunks,
                    batch_size=args.batch_size,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                    normalize_embeddings=False
                )

                # Split vectors back to each document and aggregate via mean
                vec_offset = 0
                for i, (prod_id, key, chunks, doc_text) in enumerate(zip(batch_ids, batch_keys, batch_chunks, batch_docs)):
                    num_chunks = len(chunks)
                    if num_chunks > 0:
                        doc_chunk_vectors = flat_vectors[vec_offset : vec_offset + num_chunks]
                        vec_offset += num_chunks
                        mean_vec = np.mean(doc_chunk_vectors, axis=0)
                    else:
                        mean_vec = np.zeros(1024, dtype=np.float32)

                    # L2-normalize
                    norm = np.linalg.norm(mean_vec)
                    if norm > 1e-12:
                        mean_vec = mean_vec / norm
                    mean_vec = mean_vec.astype(np.float32)
                    vec_bytes = mean_vec.tobytes()

                    # 1. Insert into sqlite-vec (delete first to prevent unique constraint error on virtual table)
                    db_conn.execute("DELETE FROM vec_dokumenty WHERE produkt_id = ?", (prod_id,))
                    db_conn.execute("INSERT INTO vec_dokumenty(produkt_id, embedding) VALUES (?, ?)", (prod_id, vec_bytes))

                    # 2. Insert into FTS5
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

                    # 3. Upload individual embedding to S3
                    if not args.skip_s3_upload and prod_id not in existing_s3_ids:
                        s3_emb_key = f"{S3_EMB_PREFIX}{prod_id}.bin"
                        s3_client.put_object(
                            Bucket=S3_BUCKET,
                            Key=s3_emb_key,
                            Body=vec_bytes,
                            ContentType="application/octet-stream"
                        )
                        uploaded_s3_count += 1

                    all_ids.append(prod_id)
                    all_embeddings.append(mean_vec)
                    processed_count += 1

            db_conn.commit()
            batch_docs.clear()
            batch_ids.clear()
            batch_keys.clear()
            batch_chunks.clear()

        for key in md_keys:
            fname = os.path.basename(key).replace(".md", "")
            try:
                prod_id = int(fname)
            except ValueError:
                prod_id = 0

            # Skip if not a valid human medicinal product
            if prod_id not in metadata_lookup:
                progress.advance(task)
                continue

            # If already processed in DB and in S3, we can skip or read cached
            if prod_id in existing_db_ids and (args.skip_s3_upload or prod_id in existing_s3_ids):
                progress.advance(task)
                continue

            # Fetch ChPL text from S3
            try:
                resp = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                doc_text = resp["Body"].read().decode("utf-8", errors="replace")
            except Exception as e:
                console.print(f"[red]Error fetching {key}: {e}[/red]")
                progress.advance(task)
                continue

            chunks, tok_count = split_text_into_chunks(doc_text, tokenizer, CHUNK_SIZE, CHUNK_OVERLAP)
            total_tokens_count += tok_count
            total_chunks_count += len(chunks)

            batch_docs.append(doc_text)
            batch_ids.append(prod_id)
            batch_keys.append(key)
            batch_chunks.append(chunks)

            # Process when batch reaches threshold
            if len(batch_docs) >= 16:
                flush_batch()

            progress.advance(task)

        # Flush remaining
        flush_batch()

    db_conn.commit()

    # Save consolidated NPZ
    if all_embeddings:
        npz_path = "data/embeddings.npz"
        console.print(f"[cyan]Saving consolidated archive to {npz_path}...[/cyan]")
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

    console.print("\n[bold green] Pipeline Run Complete![/bold green]")
    table = Table(title="ETL & Embedding Pipeline Summary", show_lines=True)
    table.add_column("Metric", style="cyan bold")
    table.add_column("Value", style="green")

    table.add_row("Processed in this run", str(processed_count))
    table.add_row("Uploaded to S3 (.bin)", str(uploaded_s3_count))
    table.add_row("Total Rows in vec_dokumenty (vec0)", str(total_vec_rows))
    table.add_row("Total Rows in fts_dokumenty (Morfeusz FTS5)", str(total_fts_rows))
    table.add_row("Total Tokens Encoded", f"{total_tokens_count:,}")
    table.add_row("Duration", f"{elapsed:.2f}s ({processed_count / max(elapsed, 0.001):.1f} docs/sec)")
    table.add_row("SQLite Unified Database", os.path.abspath(args.db))
    console.print(table)


if __name__ == "__main__":
    main()
