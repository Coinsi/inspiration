"""Opt-in physical read guards. A global read barrier bounds GC concurrency.

Nested reads on one worker reuse one connection, rather than consuming a connection
per layer. GC uses try-locks and defers rather than blocking rendering or uploads.
"""

import threading
from contextlib import contextmanager

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.storage import BlobDeletion

_local = threading.local()


def blocked(db, uri):
    return bool(
        db.scalar(
            select(BlobDeletion.id)
            .where(
                BlobDeletion.storage_uri == uri,
                BlobDeletion.status.in_(("deleting", "deleted")),
            )
            .limit(1)
        )
    )


@contextmanager
def reading(blob):
    if not settings.storage_gc_enabled:
        yield
        return
    holder = getattr(_local, "reader", None)
    if holder is None or holder["closed"]:
        db = SessionLocal()
        try:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(91357, 1)"))
        except BaseException:
            db.close()
            raise
        holder = {"db": db, "depth": 0, "closed": False}
        _local.reader = holder
    holder["depth"] += 1
    try:
        if blocked(holder["db"], blob.storage_uri):
            raise FileNotFoundError("素材物理对象正在回收，请重新上传原文件")
        yield
    finally:
        holder["depth"] -= 1
        if holder["depth"] == 0:
            holder["closed"] = True
            holder["db"].rollback()
            holder["db"].close()
