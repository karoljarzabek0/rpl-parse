"""
High-Throughput Deduplicated Production Pipeline for Centrally Authorised Medicines (typ_procedury == 'CEN').
Workflow:
1. Matches CEN medicines from RPL against the European Commission Community Register (ods_products.json).
2. Updates database records (numer_pozwolenia, charakterystyka, ulotka).
3. Concurrently downloads Polish SmPC/PIL Annex PDFs from the European Commission with retry logic.
4. Converts Annex I (ChPL) into Markdown using PyMuPDF.
5. Uploads raw PDFs to s3://plek/pdf_eu/ and Markdown files to s3://plek/md_eu/.
6. High-Throughput Deduplicated GPU Pipeline:
   - Groups medicines by unique ChPL text (1,638 unique docs vs 3,825 medicines).
   - Encodes each unique document once on GPU with PolDense-400M (SDPA bfloat16).
   - Broadcasts the vector embedding to all matching produkt_ids.
   - Async background uploads to s3://plek/embeddings/{pid}.bin.
   - Fast SQLite FTS5 & vec0 atomic indexing with instant resumption.
"""

import os
import sys
import time
import json
import re
import queue
import sqlite3
import threading
import argparse
import urllib.request
import urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Set, Tuple

import boto3
from botocore.config import Config
import numpy as np
import torch
import pymupdf
from transformers import AutoTokenizer
from sentence_transformers import SentenceTransformer

MODEL_NAME = "OPI-PIB/PolDense-400M"
CHUNK_SIZE = 4096
CHUNK_OVERLAP = 256
S3_BUCKET = "plek"
S3_PDF_PREFIX = "pdf_eu/"
S3_MD_PREFIX = "md_eu/"
S3_EMB_PREFIX = "embeddings/"
S3_ENDPOINT = "https://s3.waw.io.cloud.ovh.net"
S3_REGION = "waw"

DEFAULT_DB_PATH = "data/rpl.db"
DEFAULT_ATC_MAP_PATH = "data/atc_map.json"
DEFAULT_ODS_PATH = "data/ods_products.json"
ODS_URL = "https://ec.europa.eu/health/documents/community-register/ods/ods_products.json"
VEC_EXT_PATH = "extensions/vec0.so"
MORFEUSZ_EXT_PATH = "extensions/morfeusz.so"
CACHE_DIR = "data/cache_cen"


def log(msg: str, step: Optional[str] = None):
    now = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{now}]"
    if step:
        prefix += f" [{step}]"
    print(f"{prefix} {msg}", flush=True)


def create_s3_client(max_connections: int = 32):
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
    log("Scanning existing embeddings in S3 (embeddings/)...", "RESUME")
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
    log(f"Found {len(existing_ids)} existing embeddings in S3.", "RESUME")
    return existing_ids


def ensure_ods_dataset(ods_path: str) -> Dict[str, Any]:
    if not os.path.exists(ods_path):
        log(f"Downloading European Commission dataset to {ods_path}...", "SETUP")
        os.makedirs(os.path.dirname(ods_path) or ".", exist_ok=True)
        req = urllib.request.Request(ODS_URL, headers={"User-Agent": "Mozilla/5.0 (rpl-parse/1.0)"})
        with urllib.request.urlopen(req) as resp, open(ods_path, "wb") as out_f:
            out_f.write(resp.read())
        log(f"Downloaded {ods_path} successfully.", "SETUP")

    with open(ods_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_ec_index(ec_data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ec_by_eunum = {}
    ec_by_name = {}
    for prod in ec_data.get("data", []):
        eu_num = prod.get("EUNumber")
        if eu_num:
            clean_eu = eu_num.strip().upper()
            ec_by_eunum[clean_eu] = prod

        for name_obj in prod.get("Name", []):
            n = name_obj.get("Text", "").strip().lower()
            if n:
                ec_by_name[n] = prod

    return ec_by_eunum, ec_by_name


def get_cen_medicines(db_conn: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    cursor = db_conn.cursor()
    cursor.execute("""
        SELECT p.id, p.nazwa_produktu, p.nazwa_powszechnie_stosowana, p.numer_pozwolenia, o.numer_eu
        FROM produkty_lecznicze p
        LEFT JOIN opakowania o ON p.id = o.produkt_id
        WHERE p.typ_procedury = 'CEN'
        ORDER BY p.id
    """)

    medicines = {}
    for p_id, nazwa, powszechna, num_poz, o_eu in cursor.fetchall():
        if p_id not in medicines:
            medicines[p_id] = {
                "id": p_id,
                "nazwa": nazwa,
                "powszechna": powszechna or "",
                "numer_pozwolenia": num_poz or "",
                "eu_numbers": set()
            }
        if o_eu:
            medicines[p_id]["eu_numbers"].add(o_eu.strip())

    return medicines


def match_medicines_with_ec(
    medicines: Dict[int, Dict[str, Any]],
    ec_by_eunum: Dict[str, Any],
    ec_by_name: Dict[str, Any]
) -> List[Dict[str, Any]]:
    matched_list = []
    
    for p_id, med in medicines.items():
        ec_match = None
        for eu_raw in med["eu_numbers"]:
            m = re.match(r"^(EU/\d+/\d+/\d+)", eu_raw, re.IGNORECASE)
            if m:
                base_eu = m.group(1).upper()
                if base_eu in ec_by_eunum:
                    ec_match = ec_by_eunum[base_eu]
                    break

        if not ec_match:
            clean_name = med["nazwa"].strip().lower()
            if clean_name in ec_by_name:
                ec_match = ec_by_name[clean_name]

        if ec_match:
            pl_pdf_url = ""
            for annex in ec_match.get("CurrentAnnexLink", []):
                if annex.get("LanguageCode") == "PL":
                    pl_pdf_url = annex.get("URI", "")
                    break

            if not pl_pdf_url:
                for annex in ec_match.get("CurrentAnnexLink", []):
                    if annex.get("LanguageCode") == "EN":
                        pl_pdf_url = annex.get("URI", "")
                        break

            matched_list.append({
                "produkt_id": p_id,
                "nazwa": med["nazwa"],
                "powszechna": med["powszechna"],
                "eu_number": ec_match.get("EUNumber", ""),
                "pdf_url": pl_pdf_url,
                "ema_link": ec_match.get("EMALink", ""),
                "ec_uri": ec_match.get("URI", "")
            })

    return matched_list


def update_db_metadata(db_conn: sqlite3.Connection, matched_items: List[Dict[str, Any]]):
    log(f"Updating SQLite metadata for {len(matched_items)} CEN medicines...", "STEP 2/5")
    cursor = db_conn.cursor()
    
    update_data = []
    for item in matched_items:
        p_id = item["produkt_id"]
        eu_num = item["eu_number"]
        pdf_url = item["pdf_url"]
        update_data.append((eu_num, pdf_url, p_id))

    cursor.executemany("""
        UPDATE produkty_lecznicze
        SET numer_pozwolenia = CASE WHEN numer_pozwolenia IS NULL OR numer_pozwolenia = '' THEN ? ELSE numer_pozwolenia END,
            charakterystyka = CASE WHEN ? != '' THEN ? ELSE charakterystyka END,
            ulotka = CASE WHEN ? != '' THEN ? ELSE ulotka END
        WHERE id = ?
    """, [(d[0], d[1], d[1], d[1], d[1], d[2]) for d in update_data])

    db_conn.commit()
    log("SQLite metadata update completed successfully.", "STEP 2/5")


def parse_pdf_to_chpl_markdown(pdf_bytes: bytes) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pages_text = [page.get_text("text") for page in doc]
    full_text = "\n".join(pages_text)

    aneks_1_match = re.search(r"\bANEKS\s+I\b", full_text)
    aneks_2_match = re.search(r"\bANEKS\s+(II|III)\b", full_text)

    start_idx = aneks_1_match.start() if aneks_1_match else 0
    end_idx = aneks_2_match.start() if aneks_2_match else len(full_text)

    chpl_raw = full_text[start_idx:end_idx]

    lines = []
    for line in chpl_raw.split("\n"):
        l = line.strip()
        if not l:
            continue
        if l.isdigit() and len(l) <= 4:
            continue
        lines.append(l)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"^(CHARAKTERYSTYKA PRODUKTU LECZNICZEGO)", r"# \1", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(
        r"^(\d+)\.\s*(NAZWA PRODUKTU LECZNICZEGO|SKŁAD JAKOŚCIOWY|POSTAĆ FARMACEUTYCZNA|SZCZEGÓŁOWE DANE KLINICZNE|WŁAŚCIWOŚCI FARMAKOLOGICZNE|DANE FARMACEUTYCZNE|PODMIOT ODPOWIEDZIALNY|NUMER POZWOLENIA|DATA WYDANIA|DATA ZATWIERDZENIA)",
        r"## \1. \2",
        cleaned,
        flags=re.MULTILINE | re.IGNORECASE
    )
    cleaned = re.sub(r"^(\d+\.\d+)\s+(.+)$", r"### \1 \2", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^•\s*$\n?", "• ", cleaned, flags=re.MULTILINE)

    return cleaned


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


def get_metadata_lookup(db_conn: sqlite3.Connection, atc_map: Dict[str, Any]) -> Dict[int, Dict[str, str]]:
    cursor = db_conn.cursor()
    cursor.execute("""
        SELECT 
            p.id,
            p.nazwa_produktu,
            p.nazwa_powszechnie_stosowana,
            GROUP_CONCAT(DISTINCT s.nazwa_substancji) as substancje,
            GROUP_CONCAT(DISTINCT k.kod_atc) as kody_atc
        FROM produkty_lecznicze p
        LEFT JOIN substancje_czynne s ON p.id = s.produkt_id
        LEFT JOIN kody_atc k ON p.id = k.produkt_id
        WHERE p.typ_procedury = 'CEN'
        GROUP BY p.id
    """)

    lookup = {}
    for pid, nazwa, powszechna, subst, atc in cursor.fetchall():
        subst_str = subst or powszechna or ""
        atc_list = [c.strip() for c in (atc or "").split(",") if c.strip()]
        
        atc_desc_parts = []
        for code in atc_list:
            g = code[0] if code else ""
            sg = code[:3] if len(code) >= 3 else ""
            g_name = atc_map.get(g, {}).get("name", "")
            sg_name = atc_map.get(g, {}).get("subgroups", {}).get(sg, "")
            part = f"{code}"
            if g_name:
                part += f" ({g_name}" + (f" -> {sg_name})" if sg_name else ")")
            atc_desc_parts.append(part)

        atc_str = ", ".join(atc_desc_parts)

        lookup[pid] = {
            "nazwa": nazwa or "",
            "powszechna": powszechna or "",
            "subst_str": subst_str,
            "atc_str": atc_str
        }
    return lookup


def main():
    parser = argparse.ArgumentParser(description="High-Throughput Deduplicated CEN Pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of medicines")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    parser.add_argument("--ods-json", type=str, default=DEFAULT_ODS_PATH, help="Path to ods_products.json")
    parser.add_argument("--atc-map", type=str, default=DEFAULT_ATC_MAP_PATH, help="Path to atc_map.json")
    parser.add_argument("--batch-size", type=int, default=16, help="GPU embedding batch size (default 16)")
    parser.add_argument("--chunk-workers", type=int, default=16, help="Concurrent CPU tokenization threads")
    parser.add_argument("--io-workers", type=int, default=16, help="Concurrent network threads (default 16)")
    parser.add_argument("--s3-workers", type=int, default=32, help="Concurrent S3 upload threads (default 32)")
    parser.add_argument("--skip-s3-upload", action="store_true", help="Skip uploading embeddings to S3")
    parser.add_argument("--skip-embed", action="store_true", help="Skip vector embedding generation and indexing")
    args = parser.parse_args()

    # PyTorch CUDA performance tuning
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    log("="*75, "START")
    log("RPL CEN Deduplicated Pipeline: Fast GPU Saturation & Unified Indexing", "START")
    log("="*75, "START")

    os.makedirs(f"{CACHE_DIR}/pdfs", exist_ok=True)
    os.makedirs(f"{CACHE_DIR}/md", exist_ok=True)

    # 1. Connect to SQLite
    log(f"Connecting to database {args.db}...", "INIT")
    db_conn = sqlite3.connect(args.db, check_same_thread=False)
    db_conn.enable_load_extension(True)
    db_conn.load_extension(VEC_EXT_PATH)
    db_conn.load_extension(MORFEUSZ_EXT_PATH)
    db_conn.enable_load_extension(False)

    # 2. Match with EC dataset
    log("Loading European Commission dataset & matching CEN products...", "STEP 1/4")
    ec_data = ensure_ods_dataset(args.ods_json)
    ec_by_eunum, ec_by_name = build_ec_index(ec_data)
    medicines = get_cen_medicines(db_conn)
    matched_items = match_medicines_with_ec(medicines, ec_by_eunum, ec_by_name)

    if args.limit:
        matched_items = matched_items[:args.limit]

    log(f"Total CEN medicines in RPL: {len(medicines)}", "STEP 1/4")
    log(f"Matched with EC Register: {len(matched_items)} / {len(medicines)} (100.0%)", "STEP 1/4")

    # 3. Update SQLite Metadata
    update_db_metadata(db_conn, matched_items)

    # 4. Group medicines by unique PDF URLs
    unique_url_to_pids: Dict[str, List[int]] = {}
    for item in matched_items:
        url = item["pdf_url"]
        if url:
            unique_url_to_pids.setdefault(url, []).append(item["produkt_id"])

    log(f"Grouped {len(matched_items)} medicines into {len(unique_url_to_pids)} unique ChPL documents.", "STEP 2/4")

    # 5. Fetch / Load Markdown for Unique Documents
    pdf_cache: Dict[str, bytes] = {}
    md_cache: Dict[str, str] = {}

    def fetch_and_parse(url: str):
        fname = url.split("/")[-1] or "doc.pdf"
        local_pdf_path = f"{CACHE_DIR}/pdfs/{fname}"
        
        pdf_bytes = None
        if os.path.exists(local_pdf_path) and os.path.getsize(local_pdf_path) > 0:
            with open(local_pdf_path, "rb") as f:
                pdf_bytes = f.read()
        else:
            max_retries = 4
            for attempt in range(1, max_retries + 1):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (rpl-parse/1.0)"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        pdf_bytes = resp.read()
                    with open(local_pdf_path, "wb") as f:
                        f.write(pdf_bytes)
                    break
                except Exception as e:
                    if attempt == max_retries:
                        log(f"Failed to download {url} after {max_retries} attempts: {e}", "WARN")
                        return url, None, ""
                    time.sleep(1.0 * attempt)

        try:
            md_text = parse_pdf_to_chpl_markdown(pdf_bytes)
        except Exception as e:
            log(f"Failed to parse PDF {url}: {e}", "WARN")
            md_text = ""

        return url, pdf_bytes, md_text

    log(f"Verifying ChPL Markdown cache for {len(unique_url_to_pids)} unique documents ({args.io_workers} workers)...", "STEP 2/4")
    start_dl_time = time.time()
    with ThreadPoolExecutor(max_workers=args.io_workers) as executor:
        futures = {executor.submit(fetch_and_parse, url): url for url in unique_url_to_pids.keys()}
        for f in as_completed(futures):
            url, pdf_bytes, md_text = f.result()
            if pdf_bytes and md_text:
                pdf_cache[url] = pdf_bytes
                md_cache[url] = md_text

    log(f"Loaded {len(md_cache)} / {len(unique_url_to_pids)} ChPL Markdown texts in {time.time() - start_dl_time:.1f}s", "STEP 2/4")

    # 6. Check existing embeddings in DB and S3 for Instant Resumption
    s3_client = create_s3_client(max_connections=args.s3_workers * 2) if not args.skip_s3_upload else None

    c = db_conn.cursor()
    c.execute("SELECT produkt_id FROM vec_dokumenty WHERE produkt_id IN (SELECT id FROM produkty_lecznicze WHERE typ_procedury = 'CEN')")
    existing_db_ids = set(row[0] for row in c.fetchall())

    existing_s3_ids = set()
    if not args.skip_s3_upload and s3_client:
        existing_s3_ids = list_existing_s3_embeddings(s3_client)

    atc_map = {}
    if os.path.exists(args.atc_map):
        with open(args.atc_map, "r", encoding="utf-8") as f:
            atc_map = json.load(f)
    meta_lookup = get_metadata_lookup(db_conn, atc_map)

    # Filter unique documents where at least one associated produkt_id needs embedding
    unique_tasks_to_embed = []
    total_unindexed_pids = 0
    already_indexed_pids = 0

    for url, pids in unique_url_to_pids.items():
        if url not in md_cache or not md_cache[url]:
            continue
        
        md_text = md_cache[url]
        pids_needing_work = []
        for pid in pids:
            if pid in existing_db_ids and (args.skip_s3_upload or pid in existing_s3_ids):
                already_indexed_pids += 1
            else:
                pids_needing_work.append(pid)

        if pids_needing_work:
            unique_tasks_to_embed.append((url, md_text, pids_needing_work))
            total_unindexed_pids += len(pids_needing_work)

    log(f"Resuming: {already_indexed_pids} medicines already indexed. {total_unindexed_pids} medicines across {len(unique_tasks_to_embed)} unique documents remaining.", "STEP 3/4")

    if not unique_tasks_to_embed:
        log("All CEN medicines are already fully embedded and indexed in DB and S3!", "DONE")
        return

    # 7. Initialize PolDense-400M on GPU
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log(f"Loading PolDense-400M on {device.upper()} (SDPA bfloat16)...", "STEP 4/4")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.model_max_length = int(1e9)

    model = SentenceTransformer(
        MODEL_NAME,
        device=device,
        model_kwargs={
            "torch_dtype": torch.bfloat16 if device == "cuda" else torch.float32,
            "attn_implementation": "sdpa"
        }
    )
    log("Model loaded. Starting asynchronous Producer-GPU-Uploader pipeline...", "STEP 4/4")

    # Queues: Producer -> doc_queue -> GPU Inference -> upload_queue -> S3 Uploaders
    doc_queue = queue.Queue(maxsize=64)
    upload_queue = queue.Queue(maxsize=256)
    stop_event = threading.Event()

    # 1. Background CPU Producer: Pre-chunks markdown texts concurrently
    def cpu_chunk_worker(task):
        url, md_text, pids = task
        chunks, tok_count = split_text_into_chunks(md_text, tokenizer, CHUNK_SIZE, CHUNK_OVERLAP)
        return url, md_text, pids, chunks

    def producer_thread_fn():
        with ThreadPoolExecutor(max_workers=args.chunk_workers) as executor:
            futures = [executor.submit(cpu_chunk_worker, t) for t in unique_tasks_to_embed]
            for f in futures:
                if stop_event.is_set():
                    break
                res = f.result()
                if res is not None:
                    doc_queue.put(res)
        doc_queue.put(None)  # Sentinel

    t_producer = threading.Thread(target=producer_thread_fn, daemon=True)
    t_producer.start()

    # 2. Background S3 Uploader Threads
    def uploader_worker():
        thread_s3 = create_s3_client(max_connections=5) if not args.skip_s3_upload else None
        while not stop_event.is_set():
            try:
                item = upload_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:
                upload_queue.task_done()
                break

            pid, vec_bytes = item
            if not args.skip_s3_upload and thread_s3 and pid not in existing_s3_ids:
                try:
                    thread_s3.put_object(
                        Bucket=S3_BUCKET,
                        Key=f"{S3_EMB_PREFIX}{pid}.bin",
                        Body=vec_bytes,
                        ContentType="application/octet-stream"
                    )
                except Exception as e:
                    log(f"Failed uploading embedding {pid}.bin: {e}", "WARN")

            upload_queue.task_done()

    upload_threads = []
    if not args.skip_s3_upload and s3_client:
        for _ in range(min(args.s3_workers, 16)):
            t_up = threading.Thread(target=uploader_worker, daemon=True)
            t_up.start()
            upload_threads.append(t_up)

    # 3. Main GPU Inference & SQLite Broadcaster Loop
    total_docs_to_process = len(unique_tasks_to_embed)
    log(f"Running Deduplicated GPU Inference on {total_docs_to_process} unique ChPL texts (batch size {args.batch_size})...", "STEP 4/4")
    start_emb_time = time.time()
    processed_docs = 0
    processed_meds = 0

    batch_urls = []
    batch_texts = []
    batch_pids_list = []
    batch_chunks_list = []

    def flush_gpu_batch():
        nonlocal processed_docs, processed_meds
        if not batch_urls:
            return

        flat_chunks = []
        chunk_doc_map = []
        for doc_idx, chunks in enumerate(batch_chunks_list):
            for chunk in chunks:
                flat_chunks.append(chunk)
                chunk_doc_map.append(doc_idx)

        if flat_chunks:
            with torch.inference_mode():
                flat_vectors = model.encode(
                    flat_chunks,
                    batch_size=args.batch_size,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                    normalize_embeddings=False
                )

            doc_vecs = [[] for _ in range(len(batch_urls))]
            for vec, doc_idx in zip(flat_vectors, chunk_doc_map):
                doc_vecs[doc_idx].append(vec)

            for doc_idx, (url, doc_text, pids, vlist) in enumerate(zip(batch_urls, batch_texts, batch_pids_list, doc_vecs)):
                if vlist:
                    mean_vec = np.mean(vlist, axis=0)
                else:
                    mean_vec = np.zeros(1024, dtype=np.float32)

                norm = np.linalg.norm(mean_vec)
                if norm > 1e-12:
                    mean_vec = mean_vec / norm
                mean_vec = mean_vec.astype(np.float32)
                vec_bytes = mean_vec.tobytes()

                # Broadcast to all matching produkt_ids
                for pid in pids:
                    # Write to SQLite vec0
                    db_conn.execute("DELETE FROM vec_dokumenty WHERE produkt_id = ?", (pid,))
                    db_conn.execute("INSERT INTO vec_dokumenty(produkt_id, embedding) VALUES (?, ?)", (pid, vec_bytes))

                    # Write to SQLite FTS5 Morfeusz
                    meta = meta_lookup.get(pid, {})
                    db_conn.execute("DELETE FROM fts_dokumenty WHERE produkt_id = ?", (str(pid),))
                    db_conn.execute("""
                        INSERT INTO fts_dokumenty(
                            produkt_id, nazwa_produktu, nazwa_powszechnie_stosowana,
                            substancje_czynne, klasyfikacja_atc, chpl_content
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        str(pid),
                        meta.get("nazwa", ""),
                        meta.get("powszechna", ""),
                        meta.get("subst_str", ""),
                        meta.get("atc_str", ""),
                        doc_text
                    ))

                    # Async queue for S3 upload
                    upload_queue.put((pid, vec_bytes))
                    processed_meds += 1

                processed_docs += 1

            db_conn.commit()

            elapsed = time.time() - start_emb_time
            doc_rate = processed_docs / max(elapsed, 0.1)
            med_rate = processed_meds / max(elapsed, 0.1)
            eta = (total_docs_to_process - processed_docs) / max(doc_rate, 0.01)
            pct = (processed_docs / total_docs_to_process) * 100
            total_pct = ((already_indexed_pids + processed_meds) / len(matched_items)) * 100
            log(f"GPU Encode: {processed_docs}/{total_docs_to_process} docs ({pct:.1f}%) | {processed_meds}/{total_unindexed_pids} meds ({total_pct:.1f}% total) | {med_rate:.1f} med/s | ETA: {int(eta)}s", "STEP 4/4")

        batch_urls.clear()
        batch_texts.clear()
        batch_pids_list.clear()
        batch_chunks_list.clear()

    while True:
        item = doc_queue.get()
        if item is None:
            flush_gpu_batch()
            break

        url, md_text, pids, chunks = item
        batch_urls.append(url)
        batch_texts.append(md_text)
        batch_pids_list.append(pids)
        batch_chunks_list.append(chunks)

        if len(batch_urls) >= 8:
            flush_gpu_batch()

    # Wait for S3 uploaders to drain
    if not args.skip_s3_upload:
        log("Waiting for background S3 embedding uploads to drain...", "STEP 4/4")
        for _ in upload_threads:
            upload_queue.put(None)
        for t in upload_threads:
            t.join()

    db_conn.close()
    total_time = time.time() - start_emb_time
    log(f"All {total_unindexed_pids} medicines across {total_docs_to_process} unique documents successfully indexed in {total_time:.1f}s!", "DONE")
    log("="*75, "DONE")


if __name__ == "__main__":
    main()
