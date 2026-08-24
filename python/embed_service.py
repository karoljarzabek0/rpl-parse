"""
PolDense-400M Embedding Service & Utilities.
Handles tokenization (4096 token context, 256 token overlap) and document-level
mean vector aggregation for Rejestr Produktów Leczniczych (RPL).
"""

import os
import sys
import sqlite3
import argparse
from typing import List, Optional, Tuple, Dict, Any

import numpy as np
import torch
from transformers import AutoTokenizer
from sentence_transformers import SentenceTransformer
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rich.console import Console

console = Console()

MODEL_NAME = "OPI-PIB/PolDense-400M"
CHUNK_SIZE = 4096
CHUNK_OVERLAP = 256
STRIDE = CHUNK_SIZE - CHUNK_OVERLAP  # 3840

app = FastAPI(title="PolDense-400M Embedding Service", version="1.0.0")

_model: Optional[SentenceTransformer] = None
_tokenizer: Optional[AutoTokenizer] = None
_device: str = "cuda" if torch.cuda.is_available() else "cpu"


def get_device() -> str:
    return _device


def load_model_and_tokenizer():
    global _model, _tokenizer
    if _tokenizer is None:
        console.print(f"[cyan]Loading tokenizer: {MODEL_NAME}...[/cyan]")
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    if _model is None:
        console.print(f"[cyan]Loading model: {MODEL_NAME} on {_device}...[/cyan]")
        _model = SentenceTransformer(
            MODEL_NAME,
            device=_device,
            model_kwargs={
                "torch_dtype": torch.bfloat16 if _device == "cuda" else torch.float32,
            }
        )
        console.print(f"[green]Model loaded successfully on {_device}![/green]")
    return _model, _tokenizer


def split_text_into_chunks(
    text: str,
    tokenizer: AutoTokenizer,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP
) -> Tuple[List[str], int]:
    """
    Splits text into chunks of specified token length with overlap using the model tokenizer.
    Returns list of decoded chunk strings and total token count.
    """
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


def embed_document_chunks(
    chunks: List[str],
    model: SentenceTransformer
) -> np.ndarray:
    """
    Encodes all chunks of a document and returns the aggregated mean embedding vector (normalized to unit L2 norm).
    """
    if not chunks:
        return np.zeros(1024, dtype=np.float32)

    # Encode all chunks (ModernBERT sentence-transformers model produces 1024-d vectors)
    chunk_embeddings = model.encode(
        chunks,
        batch_size=8,
        convert_to_numpy=True,
        show_progress_bar=False,
        normalize_embeddings=False
    )

    # Document layer aggregation: take the mean of all chunk vectors
    mean_embedding = np.mean(chunk_embeddings, axis=0)

    # Normalize to unit length for fast cosine similarity via dot product
    norm = np.linalg.norm(mean_embedding)
    if norm > 1e-12:
        mean_embedding = mean_embedding / norm

    return mean_embedding.astype(np.float32)


# API Models
class EmbedChunksRequest(BaseModel):
    id: Optional[int] = None
    chunks: List[str]

class EmbedChunksResponse(BaseModel):
    id: Optional[int] = None
    chunk_count: int
    dimension: int
    embedding: List[float]

class EmbedTextRequest(BaseModel):
    id: Optional[int] = None
    text: str
    chunk_size: int = CHUNK_SIZE
    overlap: int = CHUNK_OVERLAP

class EmbedTextResponse(BaseModel):
    id: Optional[int] = None
    chunk_count: int
    total_tokens: int
    dimension: int
    embedding: List[float]

class EmbedQueryRequest(BaseModel):
    query: str

class EmbedQueryResponse(BaseModel):
    dimension: int
    embedding: List[float]


@app.on_event("startup")
def startup_event():
    load_model_and_tokenizer()


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "model": MODEL_NAME,
        "device": _device,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
    }


@app.post("/embed_chunks", response_model=EmbedChunksResponse)
def api_embed_chunks(req: EmbedChunksRequest):
    model, _ = load_model_and_tokenizer()
    if not req.chunks:
        raise HTTPException(status_code=400, detail="Chunks list cannot be empty")

    doc_embedding = embed_document_chunks(req.chunks, model)
    return EmbedChunksResponse(
        id=req.id,
        chunk_count=len(req.chunks),
        dimension=len(doc_embedding),
        embedding=doc_embedding.tolist()
    )


@app.post("/embed_text", response_model=EmbedTextResponse)
def api_embed_text(req: EmbedTextRequest):
    model, tokenizer = load_model_and_tokenizer()
    chunks, total_tokens = split_text_into_chunks(
        req.text, tokenizer, req.chunk_size, req.overlap
    )
    if not chunks:
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    doc_embedding = embed_document_chunks(chunks, model)
    return EmbedTextResponse(
        id=req.id,
        chunk_count=len(chunks),
        total_tokens=total_tokens,
        dimension=len(doc_embedding),
        embedding=doc_embedding.tolist()
    )


@app.post("/embed_query", response_model=EmbedQueryResponse)
def api_embed_query(req: EmbedQueryRequest):
    model, _ = load_model_and_tokenizer()
    # PolDense requires [query]: prefix for retrieval queries
    query_text = req.query.strip()
    if not query_text.startswith("[query]:"):
        query_text = f"[query]: {query_text}"

    query_embedding = model.encode(
        query_text,
        convert_to_numpy=True,
        show_progress_bar=False,
        normalize_embeddings=True
    )
    return EmbedQueryResponse(
        dimension=len(query_embedding),
        embedding=query_embedding.tolist()
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolDense Embedding Service")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the service on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
