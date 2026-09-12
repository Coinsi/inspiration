"""内容寻址存储(CAS):二进制按 sha256 去重。

后端可切换:
- minio:S3 兼容对象存储(生产)
- fs:本地文件系统(轻量自托管 / 开发,无需 MinIO)
"""

import hashlib
import io
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.storage import Blob

_client = None


# ── MinIO 后端 ──
def _minio():
    global _client
    if _client is None:
        import urllib3
        from minio import Minio

        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=3, read=20), retries=1),
        )
        if not _client.bucket_exists(settings.minio_bucket):
            _client.make_bucket(settings.minio_bucket)
    return _client


def _store_minio(object_name: str, data: bytes, mime: str) -> str:
    _minio().put_object(
        settings.minio_bucket, object_name, io.BytesIO(data), length=len(data), content_type=mime
    )
    return f"{settings.minio_bucket}/{object_name}"


# ── 文件系统后端 ──
def _store_fs(object_name: str, data: bytes) -> str:
    base = Path(settings.fs_storage_dir)
    path = base / object_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def put_bytes(
    db: Session, data: bytes, mime: str, *, width=None, height=None, duration_ms=None
) -> Blob:
    """写入二进制,返回 Blob(按内容哈希去重)。"""
    digest = hashlib.sha256(data).hexdigest()
    existing = db.get(Blob, digest)
    if existing is not None:
        if settings.storage_backend == "fs" and not Path(existing.storage_uri).is_file():
            # Re-uploading the same bytes can repair an unavailable legacy object.
            existing.storage_uri = _store_fs(f"{digest[:2]}/{digest}", data)
            db.flush()
        return existing

    object_name = f"{digest[:2]}/{digest}"
    if settings.storage_backend == "fs":
        uri = _store_fs(object_name, data)
    else:
        uri = _store_minio(object_name, data, mime)

    from sqlalchemy.dialects.postgresql import insert

    db.execute(
        insert(Blob)
        .values(
            hash=digest,
            storage_uri=uri,
            mime=mime,
            size_bytes=len(data),
            width=width,
            height=height,
            duration_ms=duration_ms,
        )
        .on_conflict_do_nothing(index_elements=[Blob.hash])
    )
    return db.get(Blob, digest)


def read_bytes(blob: Blob) -> bytes:
    # A verified local CAS copy can survive an unavailable legacy MinIO object.
    local_copy = Path(settings.fs_storage_dir) / blob.hash[:2] / blob.hash
    if local_copy.is_file():
        data = local_copy.read_bytes()
        if hashlib.sha256(data).hexdigest() == blob.hash:
            return data
    # Keep existing objects readable when new writes switch storage backend.
    if Path(blob.storage_uri).is_absolute() or Path(blob.storage_uri).is_file():
        return Path(blob.storage_uri).read_bytes()
    bucket, object_name = blob.storage_uri.split("/", 1)
    response = _minio().get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
