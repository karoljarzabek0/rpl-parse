"""
Dedicated Lightweight Embedding Microservice for PolDense-400M.
Runs SentenceTransformer on CPU with controlled thread pool to conserve memory and CPU.
"""

import os
import sys
import time
from typing import List, Optional

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import uvicorn

# Configure CPU concurrency
MAX_THREADS = int(os.environ.get("EMBED_NUM_THREADS", "2"))
torch.set_num_threads(MAX_THREADS)

MODEL_NAME = os.environ.get("MODEL_NAME", "OPI-PIB/PolDense-400M")

app = FastAPI(title="PolDense-400M Embedding Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

model: Optional[SentenceTransformer] = None


@app.on_event("startup")
def load_model():
    global model
    print(f"🚀 Loading embedding model '{MODEL_NAME}' on CPU (threads={MAX_THREADS})...", flush=True)
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    print(f"✅ Model loaded in {time.time() - t0:.2f}s!", flush=True)


class EmbedRequest(BaseModel):
    text: Optional[str] = None
    texts: Optional[List[str]] = None
    prefix_query: bool = True


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "loaded": model is not None,
        "threads": MAX_THREADS
    }


@app.post("/embed")
def embed(req: EmbedRequest):
    if model is None:
        raise HTTPException(status_code=503, detail="Model is still loading")

    if req.texts:
        inputs = []
        for t in req.texts:
            t_clean = t.strip()
            if req.prefix_query and not t_clean.startswith("[query]:") and not t_clean.startswith("[passage]:"):
                inputs.append(f"[query]: {t_clean}")
            else:
                inputs.append(t_clean)

        with torch.inference_mode():
            vectors = model.encode(
                inputs,
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True
            ).astype(np.float32)

        return {
            "dim": vectors.shape[1],
            "count": len(inputs),
            "embeddings": vectors.tolist()
        }

    elif req.text:
        t_clean = req.text.strip()
        if not t_clean:
            raise HTTPException(status_code=400, detail="Empty text provided")

        if req.prefix_query and not t_clean.startswith("[query]:") and not t_clean.startswith("[passage]:"):
            input_text = f"[query]: {t_clean}"
        else:
            input_text = t_clean

        with torch.inference_mode():
            vector = model.encode(
                input_text,
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True
            ).astype(np.float32)

        return {
            "dim": len(vector),
            "embedding": vector.tolist()
        }

    raise HTTPException(status_code=400, detail="Must provide 'text' or 'texts'")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="info")
