"""
Database Downloader & Initializer for RPL Container.
Fetches the compressed database backup from S3/OVH Cloud at runtime and extracts it with zstd.
"""

import os
import sys
import subprocess
import boto3
from botocore.config import Config
from botocore import UNSIGNED

DB_PATH = os.environ.get("DB_PATH", "/app/data/rpl.db")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "https://s3.waw.io.cloud.ovh.net")
S3_BUCKET = os.environ.get("S3_BUCKET", "plek")
S3_KEY = os.environ.get("S3_KEY", "backups/rpl_2026-08-25_wikidata_uses.db.zst")
AWS_REGION = os.environ.get("AWS_REGION", "waw")


def download_database():
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 1024 * 1024:
        print(f"✅ Local database already exists at {DB_PATH} ({os.path.getsize(DB_PATH) / (1024*1024):.1f} MB). Skipping download.")
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    temp_zst_path = f"{DB_PATH}.zst"

    print(f"⬇️ Downloading database from s3://{S3_BUCKET}/{S3_KEY} (Endpoint: {S3_ENDPOINT_URL})...")

    has_credentials = bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
    config = Config(
        region_name=AWS_REGION,
        s3={"addressing_style": "path"},
        signature_version=None if not has_credentials else "s3v4"
    )

    if not has_credentials:
        # Fallback to unsigned public access if bucket is public
        s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            config=Config(signature_version=UNSIGNED, s3={"addressing_style": "path"})
        )
    else:
        s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            config=config
        )

    try:
        s3.download_file(S3_BUCKET, S3_KEY, temp_zst_path)
        print(f"📦 Downloaded {os.path.getsize(temp_zst_path) / (1024*1024):.1f} MB compressed database.")
    except Exception as e:
        print(f"❌ Error downloading from S3: {e}")
        sys.exit(1)

    print(f"🗜️ Decompressing {temp_zst_path} using zstd...")
    try:
        cmd = ["zstd", "-d", "-f", temp_zst_path, "-o", DB_PATH]
        subprocess.run(cmd, check=True)
        print(f"✅ Decompressed successfully to {DB_PATH} ({os.path.getsize(DB_PATH) / (1024*1024):.1f} MB).")
    except Exception as e:
        print(f"❌ Error decompressing database: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(temp_zst_path):
            os.remove(temp_zst_path)


if __name__ == "__main__":
    download_database()
