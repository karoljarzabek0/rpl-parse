#!/usr/bin/env bash
set -e

# Script to download non-git binary and asset artifacts (.so extensions, SVG icons)
# from S3 Object Storage (bucket: plek) into local workspace.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

S3_BUCKET="s3://plek/artifacts"

echo "📦 Downloading artifacts from ${S3_BUCKET}..."

mkdir -p "${ROOT_DIR}/extensions"
mkdir -p "${ROOT_DIR}/web/public/svg"

# Check if AWS CLI or Python should be used
if command -v aws &> /dev/null; then
    echo "• Downloading extensions (.so) via aws-cli..."
    aws s3 sync "${S3_BUCKET}/extensions/" "${ROOT_DIR}/extensions/"
    
    echo "• Downloading ATC SVG icons via aws-cli..."
    aws s3 sync "${S3_BUCKET}/svg/" "${ROOT_DIR}/web/public/svg/"
else
    echo "• AWS CLI not found, using python script..."
    python3 "${SCRIPT_DIR}/download_artifacts.py"
fi

echo "✅ Artifacts successfully downloaded into:"
echo "   - ${ROOT_DIR}/extensions/"
echo "   - ${ROOT_DIR}/web/public/svg/"
