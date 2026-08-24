#!/usr/bin/env python3
"""
Python script to download non-git binary and asset artifacts (.so extensions, SVG icons)
from S3 Object Storage (bucket: plek) into local repository directories.
"""

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
EXTENSIONS_DIR = ROOT_DIR / "extensions"
SVG_DIR = ROOT_DIR / "web" / "public" / "svg"

S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL", "https://s3.waw.io.cloud.ovh.net")
S3_REGION = os.getenv("AWS_DEFAULT_REGION", "waw")
BUCKET_NAME = "plek"


def download_artifacts():
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        print("boto3 is not installed. Installing or running in venv...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "boto3"])
        import boto3
        from botocore.config import Config

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        config=Config(s3={"addressing_style": "path"})
    )

    EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)
    SVG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"📦 Fetching artifact list from s3://{BUCKET_NAME}/artifacts/...")
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET_NAME, Prefix="artifacts/")

    count = 0
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue

            # Determine local target
            if key.startswith("artifacts/extensions/"):
                rel_path = key[len("artifacts/extensions/"):]
                local_file = EXTENSIONS_DIR / rel_path
            elif key.startswith("artifacts/svg/"):
                rel_path = key[len("artifacts/svg/"):]
                local_file = SVG_DIR / rel_path
            else:
                continue

            local_file.parent.mkdir(parents=True, exist_ok=True)
            print(f"  &bull; Downloading {key} -> {local_file.relative_to(ROOT_DIR)}")
            s3.download_file(BUCKET_NAME, key, str(local_file))
            count += 1

    print(f"✅ Downloaded {count} artifacts successfully.")


if __name__ == "__main__":
    download_artifacts()
