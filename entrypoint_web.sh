#!/usr/bin/env bash
set -e

cleanup() {
    echo "🛑 Shutting down powlekane.pl services..."
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
echo "🚀 Starting powlekane.pl Web & Search Engine"
echo "=================================================="

# 1. Launch FastAPI backend on port 8000
echo "🧠 Starting FastAPI Search Engine on port 8000..."
cd /app
python python/api_server.py &
API_PID=$!

# 2. Wait for FastAPI backend to be ready
echo "⏳ Waiting for API backend to initialize..."
MAX_RETRIES=30
RETRY_COUNT=0
until curl -s -f http://127.0.0.1:8000/api/stats > /dev/null 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "❌ Timeout waiting for FastAPI backend."
        kill -9 "$API_PID" 2>/dev/null || true
        exit 1
    fi
    sleep 0.5
done

echo "✅ FastAPI Backend is ready!"

# 3. Start Bun SSR Web Server on port 3000
echo "🌐 Starting Bun Web Server on port ${PORT:-3000}..."
cd /app/web
bun run server.ts &
BUN_PID=$!

# 4. Wait for background services
wait -n $API_PID $BUN_PID
cleanup
