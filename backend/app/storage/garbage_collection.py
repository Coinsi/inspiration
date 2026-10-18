"""Explicit, opt-in GC operations. No scheduler or sweeping of user content.

Mark -> grace -> guarded recheck -> durable deleting -> idempotent physical delete.
Every operation names a captured URI; a re-upload can publish a new URI safely.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text

from app.core.config import settings
from app.models.storage import Blob, BlobDeletion
from app.storage import cas, gc_guards, retention


class Deferred(RuntimeError):
    pass


def enabled():
    if not settings.storage_gc_enabled:
        raise RuntimeError("Physical GC is disabled; enable read guards on all services first")


def mark(db, digest, *, grace_seconds=86400):
    enabled()
    if grace_seconds < 0:
        raise ValueError("Invalid grace period")
    if retention.inspect_blob(db, digest)["retained"]:
        raise ValueError("Content is still retained")
    blob = db.get(Blob, digest)
    if not blob:
        raise ValueError("Unknown content identity")
    operation = BlobDeletion(
        blob_hash=digest,
        storage_uri=blob.storage_uri,
        status="marked",
        not_before=datetime.now(UTC) + timedelta(seconds=grace_seconds),
    )
    db.add(operation)
    db.flush()
    return operation.id


def claim(db, operation_id):
    enabled()
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(91357, 1)")):
        raise Deferred("Media readers are active")
    operation = db.scalar(
        select(BlobDeletion).where(BlobDeletion.id == operation_id).with_for_update(nowait=True)
    )
    if not operation:
        raise ValueError("Unknown deletion operation")
    if operation.status in ("deleted", "canceled"):
        return operation
    if operation.status == "deleting":
        return operation  # Durable tombstone already protects references and reads.
    if operation.not_before > datetime.now(UTC):
        raise Deferred("Retention grace period has not elapsed")
    key = int.from_bytes(bytes.fromhex(operation.blob_hash[:16]), "big", signed=True)
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
        raise Deferred("A CAS writer is active")
    gc_guards.verify_and_lock(db)
    blob = db.scalar(
        select(Blob).where(Blob.hash == operation.blob_hash).with_for_update(nowait=True)
    )
    if (
        not blob
        or blob.storage_uri != operation.storage_uri
        or retention.inspect_blob(db, operation.blob_hash)["retained"]
    ):
        operation.status = "canceled"
        return operation
    operation.status = "deleting"
    operation.last_error = None
    db.flush()
    return operation


def delete_object(operation):
    """Only a captured CAS key inside the configured storage may be removed."""
    digest = operation.blob_hash
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid object hash")
    path = Path(operation.storage_uri)
    if path.is_absolute() or path.is_file():
        root = Path(settings.fs_storage_dir).resolve()
        resolved = path.resolve()
        resolved.relative_to(root)
        if resolved.parent != root / digest[:2] or not re.fullmatch(
            digest + r"(?:\.[0-9a-f]{32})?", resolved.name
        ):
            raise ValueError("Refusing a non-CAS filesystem path")
        resolved.unlink(missing_ok=True)
    else:
        bucket, key = operation.storage_uri.split("/", 1)
        if bucket != settings.minio_bucket or not re.fullmatch(
            digest[:2] + "/" + digest + r"(?:\.[0-9a-f]{32})?", key
        ):
            raise ValueError("Refusing a non-CAS object key")
        cas._minio().remove_object(bucket, key)


def execute(sessions, operation_id):
    enabled()
    with sessions() as db:
        operation = claim(db, operation_id)
        status = operation.status
        db.commit()  # Persist deletion identity BEFORE any physical action.
    if status != "deleting":
        return status
    try:
        with sessions() as db:
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(91357, 1)")):
                raise Deferred("Media readers are active")
            operation = db.scalar(
                select(BlobDeletion)
                .where(BlobDeletion.id == operation_id)
                .with_for_update(nowait=True)
            )
            if operation.status != "deleting":
                return operation.status
            gc_guards.verify_and_lock(db)
            blob = db.get(Blob, operation.blob_hash)
            if (
                blob
                and blob.storage_uri == operation.storage_uri
                and retention.inspect_blob(db, blob.hash)["retained"]
            ):
                raise RuntimeError("Unexpected retained source under a deletion tombstone")
            delete_object(operation)
            operation.status = "deleted"
            operation.last_error = None
            db.commit()
            return "deleted"
    except Exception as exc:
        with sessions() as db:
            operation = db.get(BlobDeletion, operation_id)
            if operation and operation.status == "deleting":
                operation.last_error = type(exc).__name__
                db.commit()
        raise
