# ==============================================================================
# Multi-Stage Lean Dockerfile for RPL Hybrid Search Engine
# Includes: Python 3.12, PyTorch CPU, PolDense-400M, Bun.js SSR, SQLite Extensions
# ==============================================================================

# 1. Grab official Bun runtime binary
FROM oven/bun:1-slim AS bun-runtime

# 2. Main Application Image
FROM python:3.12-slim-bookworm

LABEL maintainer="karol@example.com"
LABEL description="RPL Medical Hybrid Search Engine (FastAPI + Bun.js + PolDense-400M)"

# Set execution environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=3000 \
    API_BACKEND="http://127.0.0.1:8000" \
    S3_ENDPOINT_URL="https://s3.waw.io.cloud.ovh.net" \
    S3_BUCKET="plek" \
    S3_KEY="backups/rpl_2026-08-25_wikidata_uses.db.zst" \
    DB_PATH="/app/data/rpl.db" \
    LD_LIBRARY_PATH="/app/extensions:/usr/lib:${LD_LIBRARY_PATH}" \
    HF_HOME="/app/cache/huggingface"

# Install essential runtime tools (zstd for decompression, curl for healthchecks, OpenMP for PyTorch CPU)
RUN apt-get update && apt-get install -y --no-install-recommends \
    zstd \
    curl \
    ca-certificates \
    libstdc++6 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy Bun binary from official image
COPY --from=bun-runtime /usr/local/bin/bun /usr/local/bin/bun

WORKDIR /app

# Install Lean CPU-only PyTorch & Python dependencies (layer cached)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir \
    transformers>=4.48.0 \
    sentence-transformers>=3.4.0 \
    fastapi>=0.115.0 \
    uvicorn>=0.30.0 \
    numpy>=1.26.0 \
    boto3>=1.34.0 \
    requests>=2.31.0 \
    rich>=13.0.0 \
    sqlite-vec>=0.1.9

# Copy SQLite extensions (vec0, morfeusz, libmorfeusz2)
COPY extensions/ /app/extensions/

# Copy ATC taxonomies and dictionaries
COPY data/atc_map.json data/atc_hierarchy.json /app/data/

# Copy Python backend code
COPY python/ /app/python/

# Copy Scripts & Entrypoint
COPY scripts/ /app/scripts/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Copy Web frontend (Bun.js & static assets)
COPY web/ /app/web/

# Create data & cache directories
RUN mkdir -p /app/data /app/cache/huggingface

EXPOSE 3000

# Persistent volume for downloaded database and model cache
VOLUME ["/app/data", "/app/cache"]

ENTRYPOINT ["/app/entrypoint.sh"]
