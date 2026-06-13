"""One-shot: copy all MinIO-stored blobs into the local fs CAS, then repoint the DB.

Why: blobs created while STORAGE_BACKEND=minio have storage_uri like
"inspiration/<shard>/<hash>" (a MinIO object key). After switching to fs they
can't be served. This reads each object from MinIO and writes it to the fs CAS
(./_storage/<shard>/<hash>), then updates blob.storage_uri to the fs path.

Prereq: MinIO must be running once (e.g. `cd deploy && docker compose up -d minio`).
Run from the backend dir:  .\.venv\Scripts\python.exe scripts\migrate-minio-to-fs.py
After it finishes you can stop Docker for good (STORAGE_BACKEND stays fs).
"""
import os
import sys

import psycopg
from minio import Minio

DB = os.environ.get("DATABASE_URL", "postgresql://postgres:123456@localhost:5432/inspiration")
# psycopg wants a libpq URL (no +psycopg driver suffix)
DB = DB.replace("postgresql+psycopg://", "postgresql://")
ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
ACCESS = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
SECRET = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
BUCKET = os.environ.get("MINIO_BUCKET", "inspiration")
FS_DIR = os.environ.get("FS_STORAGE_DIR", "./_storage")


def main() -> int:
    m = Minio(ENDPOINT, access_key=ACCESS, secret_key=SECRET, secure=False)
    if not m.bucket_exists(BUCKET):
        print(f"MinIO bucket '{BUCKET}' not found at {ENDPOINT}. Is MinIO running?")
        return 1

    conn = psycopg.connect(DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT hash, storage_uri FROM blob WHERE storage_uri LIKE %s", (f"{BUCKET}/%",))
    rows = cur.fetchall()
    print(f"{len(rows)} MinIO-backed blob(s) to migrate -> {os.path.abspath(FS_DIR)}")

    migrated = skipped = failed = 0
    for h, uri in rows:
        key = uri.split("/", 1)[1]  # "<shard>/<hash>"
        dst = os.path.join(FS_DIR, *key.split("/"))
        try:
            if not os.path.exists(dst):
                data = m.get_object(BUCKET, key).read()
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(data)
            new_uri = os.path.join(FS_DIR, *key.split("/"))
            cur.execute("UPDATE blob SET storage_uri = %s WHERE hash = %s", (new_uri, h))
            migrated += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED {h[:12]}: {e}")
            failed += 1

    print(f"done: migrated={migrated} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
