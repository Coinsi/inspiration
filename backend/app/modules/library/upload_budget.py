"""Admission limits for retained upload files, independent of ML worker limits."""

import shutil
from pathlib import Path

from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.errors import Conflict
from app.models.library import MediaVersion

UPLOAD_BUDGET_LOCK = 726381913


def check_space(additional_bytes):
    folder = Path(settings.library_staging_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < additional_bytes + settings.library_staging_reserve_bytes:
        raise Conflict("素材暂存空间不足，上传进度已保留，请释放空间后继续")


def reserve(db, size_bytes):
    # A transaction-scoped lock makes simultaneous upload admission atomic.
    # Failed uploads retain their source for retries and still consume the budget.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": UPLOAD_BUDGET_LOCK})
    reserved, remaining = db.execute(
        select(
            func.coalesce(func.sum(MediaVersion.size_bytes), 0),
            func.coalesce(func.sum(MediaVersion.size_bytes - MediaVersion.uploaded_bytes), 0),
        ).where(MediaVersion.status.in_(("uploading", "queued", "processing", "failed")))
    ).one()
    if reserved + size_bytes > settings.library_staging_max_bytes:
        raise Conflict("上传暂存队列容量已满，请等待处理完成或取消不需要的上传")
    check_space(remaining + size_bytes)
