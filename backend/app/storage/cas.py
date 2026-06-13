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
        from minio import Minio

        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
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
        return existing

    object_name = f"{digest[:2]}/{digest}"
    if settings.storage_backend == "fs":
        uri = _store_fs(object_name, data)
    else:
        uri = _store_minio(object_name, data, mime)

    blob = Blob(
        hash=digest, storage_uri=uri, mime=mime, size_bytes=len(data),
        width=width, height=height, duration_ms=duration_ms,
    )
    db.add(blob)
    db.flush()
    return blob


def read_bytes(blob: Blob) -> bytes:
    if settings.storage_backend == "fs":
        return Path(blob.storage_uri).read_bytes()
    object_name = blob.storage_uri.split("/", 1)[1]
    return _minio().get_object(settings.minio_bucket, object_name).read()
