#!/usr/bin/env bash
set -e

# Graceful shutdown handler
cleanup() {
    echo "🛑 Shutting down RPL services..."
    if [ -n "$API_PID" ]; then
        kill -TERM "$API_PID" 2>/dev/null || true
    fi
    if [ -n "$BUN_PID" ]; then
        kill -TERM "$BUN_PID" 2>/dev/null || true
    fi
    wait
    echo "👋 Shutdown complete."
    exit 0
}

trap cleanup SIGTERM SIGINT

echo "=================================================="
echo "🚀 Starting RPL Search & Medical Intelligence App"
echo "=================================================="

# 1. Fetch & decompress SQLite database from S3 if needed
python /app/scripts/download_db.py

# 2. Launch FastAPI backend with PolDense-400M (CPU)
echo "🧠 Starting FastAPI Search Engine on port 8000..."
cd /app
python python/api_server.py &
API_PID=$!

# 3. Wait for FastAPI backend to be ready
echo "⏳ Waiting for API and PolDense-400M model to load..."
MAX_RETRIES=40
RETRY_COUNT=0
until curl -s -f http://127.0.0.1:8000/api/stats > /dev/null 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "❌ Timeout waiting for FastAPI backend to initialize."
        kill -9 "$API_PID" 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

echo "✅ FastAPI Search Engine is ready!"

# 4. Start Bun SSR Web Server
echo "🌐 Starting Bun Web Server on port ${PORT:-3000}..."
cd /app/web
bun run server.ts &
BUN_PID=$!

# 5. Wait for background services
wait -n $API_PID $BUN_PID
cleanup
