"""
Batch embedding generator for Polish Medicinal Products Registry (RPL).
Fetches markdown ChPL files from S3, tokenizes into 4096-token chunks with 256-token overlap,
computes PolDense-400M embeddings on GPU, takes the mean vector per document, and saves to SQLite & NPZ.
"""

import os
import sys
import time
import sqlite3
import argparse
from typing import List, Dict, Any, Optional

import boto3
from botocore.config import Config
import numpy as np
import torch
from transformers import AutoTokenizer
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

console = Console()

MODEL_NAME = "OPI-PIB/PolDense-400M"
CHUNK_SIZE = 4096
CHUNK_OVERLAP = 256
S3_BUCKET = "plek"
S3_PREFIX = "md/"
S3_ENDPOINT = "https://s3.waw.io.cloud.ovh.net"
S3_REGION = "waw"


def get_s3_client():
    """Create S3 client using existing AWS config/credentials and OVHcloud endpoint."""
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        config=Config(
            s3={"addressing_style": "path"},
            max_pool_connections=25,
            retries={"max_attempts": 5, "mode": "standard"}
        )
    )


def init_embeddings_db(db_path: str):
    """Initializes the SQLite schema for storing document embeddings."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dokumenty_embeddings (
        produkt_id INTEGER PRIMARY KEY,
        filename TEXT NOT NULL,
        chunk_count INTEGER NOT NULL,
        total_tokens INTEGER NOT NULL,
        doc_char_length INTEGER NOT NULL,
        embedding BLOB NOT NULL, -- Float32 little-endian binary blob (1024 * 4 bytes)
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emb_produkt_id ON dokumenty_embeddings(produkt_id)")
    conn.commit()
    conn.close()


def list_s3_md_files(s3_client, bucket: str, prefix: str, limit: int = 100) -> List[str]:
    """Lists up to `limit` markdown file keys from S3."""
    console.print(f"[cyan]Listing up to {limit} documents from S3 bucket '{bucket}' (prefix: '{prefix}')...[/cyan]")
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".md") and key != prefix:
                keys.append(key)
                if len(keys) >= limit:
                    return keys
    return keys


def split_text_into_chunks(text: str, tokenizer: AutoTokenizer, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    """Splits text into chunks of `chunk_size` tokens with `overlap` tokens using tokenizer."""
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
    parser = argparse.ArgumentParser(description="Generate PolDense embeddings for RPL documents")
    parser.add_argument("--limit", type=int, default=100, help="Number of documents to process")
    parser.add_argument("--db", type=str, default="data/embeddings.db", help="Path to SQLite embeddings DB")
    parser.add_argument("--npz", type=str, default="data/embeddings.npz", help="Path to output NPZ file")
    parser.add_argument("--meta-db", type=str, default="data/demo_rpl.db", help="Path to demo_rpl.db for product metadata")
    args = parser.parse_args()

    console.rule("[bold green]PolDense-400M RPL Document Embedding Pipeline[/bold green]")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    console.print(f"• Hardware Device: [bold green]{device.upper()}[/bold green]" + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else ""))
    console.print(f"• Embedding Model: [bold yellow]{MODEL_NAME}[/bold yellow]")
    console.print(f"• Context Window: [bold yellow]{CHUNK_SIZE} tokens[/bold yellow] (Overlap: {CHUNK_OVERLAP} tokens)")
    console.print(f"• Aggregation: [bold yellow]Document Layer Mean Vector (1024-d)[/bold yellow]")
    console.print(f"• Target Sample Count: [bold yellow]{args.limit}[/bold yellow] documents")

    # 1. Initialize S3
    s3_client = get_s3_client()
    md_keys = list_s3_md_files(s3_client, S3_BUCKET, S3_PREFIX, limit=args.limit)
    console.print(f"[green]Found {len(md_keys)} documents to embed.[/green]\n")

    # 2. Initialize Database
    init_embeddings_db(args.db)

    # 3. Load Tokenizer & Model
    console.print("[cyan]Loading HuggingFace Tokenizer & ModernBERT model...[/cyan]")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = SentenceTransformer(
        MODEL_NAME,
        device=device,
        model_kwargs={
            "torch_dtype": torch.bfloat16 if device == "cuda" else torch.float32,
        }
    )
    console.print("[green]Tokenizer and model ready![/green]\n")

    # 4. Processing Loop
    processed_ids = []
    processed_embeddings = []
    chunk_counts = []
    token_counts = []
    
    start_time = time.time()
    db_conn = sqlite3.connect(args.db)
    cursor = db_conn.cursor()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Processing & Embedding documents...", total=len(md_keys))

        for key in md_keys:
            # Extract product ID from filename (e.g., md/100000014.md -> 100000014)
            filename = os.path.basename(key)
            prod_id_str = filename.replace(".md", "")
            try:
                prod_id = int(prod_id_str)
            except ValueError:
                prod_id = 0

            # Fetch document from S3
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
            doc_text = response["Body"].read().decode("utf-8", errors="replace")

            # Tokenize & Chunk
            chunks, total_tokens = split_text_into_chunks(doc_text, tokenizer, CHUNK_SIZE, CHUNK_OVERLAP)
            if not chunks:
                progress.advance(task)
                continue

            # Encode all chunks for this document
            chunk_vectors = model.encode(
                chunks,
                batch_size=8,
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=False
            )

            # Aggregate chunk vectors -> Mean vector per document
            doc_vector = np.mean(chunk_vectors, axis=0)

            # L2 normalize mean vector
            norm = np.linalg.norm(doc_vector)
            if norm > 1e-12:
                doc_vector = doc_vector / norm

            doc_vector = doc_vector.astype(np.float32)

            # Save to SQLite
            cursor.execute("""
            INSERT OR REPLACE INTO dokumenty_embeddings (
                produkt_id, filename, chunk_count, total_tokens, doc_char_length, embedding
            ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                prod_id,
                filename,
                len(chunks),
                total_tokens,
                len(doc_text),
                doc_vector.tobytes()
            ))

            processed_ids.append(prod_id)
            processed_embeddings.append(doc_vector)
            chunk_counts.append(len(chunks))
            token_counts.append(total_tokens)

            progress.advance(task)

    db_conn.commit()
    db_conn.close()

    elapsed = time.time() - start_time
    emb_matrix = np.array(processed_embeddings, dtype=np.float32)
    ids_array = np.array(processed_ids, dtype=np.int64)

    # Save to NPZ for rapid loading in search apps
    np.savez_compressed(
        args.npz,
        ids=ids_array,
        embeddings=emb_matrix,
        chunk_counts=np.array(chunk_counts),
        token_counts=np.array(token_counts)
    )

    console.print("\n[bold green] Embedding Generation Complete![/bold green]")

    # Print summary statistics
    table = Table(title="PolDense-400M Embeddings Summary", show_lines=True)
    table.add_column("Metric", style="cyan bold")
    table.add_column("Value", style="green")

    table.add_row("Processed Documents", str(len(processed_ids)))
    table.add_row("Total Chunks Encoded", str(sum(chunk_counts)))
    table.add_row("Total Tokens Processed", f"{sum(token_counts):,}")
    table.add_row("Avg Tokens per Document", f"{np.mean(token_counts):.1f}")
    table.add_row("Avg Chunks per Document", f"{np.mean(chunk_counts):.2f} (max: {max(chunk_counts) if chunk_counts else 0})")
    table.add_row("Embedding Matrix Shape", f"{emb_matrix.shape} (float32)")
    table.add_row("Total Time", f"{elapsed:.2f}s ({len(processed_ids)/elapsed:.1f} docs/sec)")
    table.add_row("SQLite Database", os.path.abspath(args.db))
    table.add_row("NumPy NPZ Archive", os.path.abspath(args.npz))

    console.print(table)


if __name__ == "__main__":
    main()
