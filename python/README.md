# RPL Vector Search & Embeddings

Package and tools for generating dense text embeddings and running semantic similarity search on Polish Medicinal Products Registry (Rejestr Produktów Leczniczych - RPL).

## Architecture

- **Embedding Model:** [`OPI-PIB/PolDense-400M`](https://huggingface.co/OPI-PIB/PolDense-400M) (ModernBERT, 1024-dimensional dense vectors)
- **Context Window:** 4096 tokens with 256 tokens overlap
- **Document Aggregation:** Mean pooling across all chunk vectors for each document
- **Query Prefix:** `[query]: ` (per PolDense retrieval specification)
- **ATC Classification:** Hierarchical description mapping from `data/atc_map.json`

## Setup

```bash
cd python
uv venv -p $(which python) --system-site-packages
uv pip install -r pyproject.toml
```

## Tools

### 1. Interactive Vector Search App
```bash
# Single CLI query with ATC classification
python search_app.py -q "lek na ból głowy i stany zapalne"

# Interactive CLI mode
python search_app.py
```

### 2. Standalone Sample Embeddings Generator
```bash
# Fetch and embed 100 sample documents from S3
python generate_sample_embeddings.py --limit 100
```

### 3. GPU Embedding Service (for Rust pipeline)
```bash
# Run FastAPI embedding service on port 8000
python embed_service.py --port 8000
```
