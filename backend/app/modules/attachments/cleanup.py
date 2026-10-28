"""Conservative draft expiry; saved/hidden conversation files are never expired."""

import heapq
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.attachment import Attachment
from app.modules.attachments.service import cleanup_staging

log = logging.getLogger(__name__)
BATCH_SIZE = 100


def expire_drafts(db, *, now=None):
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.attachment_draft_ttl_days)
    rows = list(
        db.scalars(
            select(Attachment)
            .where(
                Attachment.saved_at.is_(None),
                Attachment.message_id.is_(None),
                Attachment.conversation_id.is_(None),
                Attachment.updated_at < cutoff,
                Attachment.status.in_(("uploading", "processing", "ready", "failed")),
            )
            .order_by(Attachment.updated_at, Attachment.id)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    )
    for item in rows:
        item.status = "cancelled"
        item.error = "未发送的附件已过期，请重新添加"
        item.blob_hash = item.thumbnail_hash = None
    db.flush()
    return [item.id for item in rows]


def cleanup_leftovers(sessions, *, now=None):
    """Recover a process exit after DB commit but before staged-file unlink."""
    folder = Path(settings.library_staging_dir).resolve() / "attachments"
    if not folder.is_dir():
        return
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.attachment_draft_ttl_days)

    # Work on a bounded oldest set without materializing all directory entries in RAM.
    def modified(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return float("inf")

    paths = heapq.nsmallest(BATCH_SIZE, folder.glob("*.upload"), key=modified)
    with sessions() as db:
        for path in paths:
            try:
                identifier = uuid.UUID(path.stem)
            except ValueError:
                continue
            item = db.scalar(select(Attachment).where(Attachment.id == identifier))
            if item and item.status in ("ready", "cancelled"):
                cleanup_staging(identifier)
            elif item is None and modified(path) < cutoff.timestamp():
                cleanup_staging(identifier)


def run_once(sessions=SessionLocal, *, now=None):
    with sessions() as db:
        identifiers = expire_drafts(db, now=now)
        db.commit()
    # Publication of cancellation must precede removal of recoverable bytes.
    for identifier in identifiers:
        cleanup_staging(identifier)
    cleanup_leftovers(sessions, now=now)
    return len(identifiers)


def start():
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                count = run_once()
                delay = 1 if count == BATCH_SIZE else 3600
            except Exception:
                log.warning("Attachment cleanup unavailable; check database migrations and storage")
                delay = 60
            stop.wait(delay)

    thread = threading.Thread(target=loop, name="attachment-cleanup", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=5)

    shutdown.is_alive = thread.is_alive
    return shutdown
