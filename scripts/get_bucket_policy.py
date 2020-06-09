#!/usr/bin/env python
import os

from minio import Minio

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
MINIO_SECURE = os.getenv("MINIO_SECURE")
MINIO_BUCKET = os.getenv("MINIO_BUCKET")

if not MINIO_ENDPOINT or not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY or not MINIO_BUCKET:
    print(
        "Error: Environment variables not set. MINIO_ENDPOINT, MINIO_ACCESS_KEY, "
        "MINIO_SECRET_KEY and MINIO_BUCKET must be set."
    )

if MINIO_SECURE and MINIO_SECURE.lower() == "false":
    MINIO_SECURE = False
else:
    MINIO_SECURE = True

minio_client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)

policy = minio_client.get_bucket_policy(MINIO_BUCKET)
print(policy)
