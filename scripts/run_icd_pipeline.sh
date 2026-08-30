#!/usr/bin/env bash
set -e

echo "=== Starting Full RPL Medical Conditions & ICD-11 / ICD-10 Pipeline ==="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"
PYTHON_BIN="python/.venv/bin/python"

if [ ! -f "${PYTHON_BIN}" ]; then
  PYTHON_BIN="python3"
fi

"${PYTHON_BIN}" python/run_full_icd_pipeline.py
