"""Conservative capacity reservations for one persistent archive staging volume."""

import shutil
from pathlib import Path

from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.errors import Conflict
from app.models.engineering import EngineeringArchiveJob
from app.models.storage import Blob
from app.models.work import CreativeWork
from app.modules.engineering import packages

OVERHEAD = 32 * 1024**2


def export_size(db, row):
    package = packages.export(db, row.work_id, row.snapshot)
    size = sum(db.get(Blob, digest).size_bytes for digest in package["external_media"])
    return size * 2 + OVERHEAD  # Verified sources plus finished ZIP.


def reserve(db, row, amount):
    # Always take task row before the shared quota lock, including retry/growth.
    db.flush()
    db.execute(
        select(EngineeringArchiveJob.id).where(EngineeringArchiveJob.id == row.id).with_for_update()
    )
    db.refresh(row)
    db.execute(text("SELECT pg_advisory_xact_lock(91357, 3)"))
    prior = row.reserved_bytes or 0
    work = db.get(CreativeWork, row.work_id)
    total = db.scalar(select(func.coalesce(func.sum(EngineeringArchiveJob.reserved_bytes), 0)))
    project = db.scalar(
        select(func.coalesce(func.sum(EngineeringArchiveJob.reserved_bytes), 0))
        .join(CreativeWork, CreativeWork.id == EngineeringArchiveJob.work_id)
        .where(CreativeWork.project_id == work.project_id)
    )
    extra = max(0, amount - prior)
    if total + extra > settings.archive_staging_quota_bytes:
        raise Conflict("归档暂存总容量不足，请等待过期任务释放空间或调整容量配置")
    if project + extra > settings.archive_project_quota_bytes:
        raise Conflict("该项目归档暂存容量已达上限")
    root = Path(settings.fs_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Subtract all reservations conservatively, including bytes already on disk.
    # This may reject early but does not promise the same free bytes to two jobs.
    if shutil.disk_usage(root).free < total + extra + settings.archive_disk_headroom_bytes:
        raise Conflict("归档磁盘可用空间不足，请释放空间后重试")
    row.reserved_bytes = max(amount, prior)
    db.flush()
