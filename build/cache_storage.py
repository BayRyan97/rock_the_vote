#!/usr/bin/env python3
"""
Upload or download FEC+NYBOE+NYCCFB cache files to/from Supabase Storage.

Usage:
    python build/cache_storage.py upload    # local → Supabase Storage
    python build/cache_storage.py download  # Supabase Storage → local

Requires env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import os
import sys
from pathlib import Path

import requests

BUCKET = "donation-cache"
FILES = ["fec_cache.json", "nyboe_cache.json", "nyccfb_cache.json"]
DATA = Path(__file__).resolve().parent.parent / "data"


def _headers():
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
    }


def _base():
    return os.environ["SUPABASE_URL"].rstrip("/") + f"/storage/v1/object/{BUCKET}"


def upload():
    for fname in FILES:
        path = DATA / fname
        if not path.exists():
            print(f"SKIP {fname} (not found locally)")
            continue
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"Uploading {fname} ({size_mb:.1f} MB)…", flush=True)
        with open(path, "rb") as f:
            r = requests.post(
                f"{_base()}/{fname}",
                headers={**_headers(), "x-upsert": "true"},
                data=f,
                timeout=600,
            )
        r.raise_for_status()
        print(f"  ✓ {fname} uploaded")


def download():
    for fname in FILES:
        print(f"Downloading {fname}…", flush=True)
        r = requests.get(
            f"{_base()}/{fname}",
            headers=_headers(),
            timeout=600,
            stream=True,
        )
        if r.status_code == 404:
            print(f"  SKIP {fname} (not in storage — cold start)")
            continue
        r.raise_for_status()
        dest = DATA / fname
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
        size_mb = dest.stat().st_size / 1024 / 1024
        print(f"  ✓ {fname} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("upload", "download"):
        print("Usage: cache_storage.py upload|download")
        sys.exit(1)
    if sys.argv[1] == "upload":
        upload()
    else:
        download()
