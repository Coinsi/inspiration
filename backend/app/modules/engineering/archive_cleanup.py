"""Reclaim only known private archive files, after durable transition to purging."""

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select, text

from app.core.config import settings
from app.models.engineering import EngineeringArchiveJob
from app.modules.engineering.archive_transfer import ownership as file_ownership


def remove_files(job_id, *, incomplete_only=False):
    root = Path(settings.fs_storage_dir).resolve() / "_engineering_exports"
    folder = root / str(job_id)
    if root.resolve() != root or folder.resolve() != folder:
        raise ValueError("Archive staging may not redirect outside its configured root")
    if not folder.exists():
        return
    if folder.is_symlink() or folder.resolve().parent != root.resolve():
        raise ValueError("Unsafe archive staging directory")
    files, directories = [], []
    for child in folder.iterdir():
        if child.is_symlink() or child.resolve().parent != folder.resolve():
            raise ValueError("Unsafe archive staging entry")
        if child.is_dir():
            if not child.name.startswith("inspiration-import-"):
                raise ValueError("Unknown staging directory; refusing cleanup")
            for nested in child.iterdir():
                if (
                    nested.is_symlink()
                    or not nested.is_file()
                    or not re.fullmatch(r"[0-9a-f]{64}", nested.name)
                ):
                    raise ValueError("Unknown imported staging entry")
                if nested.resolve().parent != child.resolve():
                    raise ValueError("Unsafe imported staging path")
                files.append(nested)
            directories.append(child)
        elif child.name != "worker.lock":
            if not (
                child.name in {"manifest.json", "manifest.pending", "result.zip", "upload.zip"}
                or re.fullmatch(r"[0-9a-f]{64}(?:\.partial)?", child.name)
                or re.fullmatch(r"inspiration-package-[a-zA-Z0-9_-]+\.zip", child.name)
            ):
                raise ValueError("Unknown staging file; refusing cleanup")
            files.append(child)
    # Validate the entire set before deleting any files. Never recursively delete a supplied path.
    for path in files:
        if (
            incomplete_only
            and path.parent == folder
            and not (path.name.startswith("inspiration-package-") or path.name == "result.zip")
        ):
            continue
        path.unlink(missing_ok=True)
    for path in directories:
        path.rmdir()


def cleanup(sessions, job_id, *, now=None):
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=max(1, settings.archive_retention_days))
    key = int.from_bytes(job_id.bytes[:8], "big", signed=True)
    with sessions() as guard:
        if not guard.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
            return False  # An executor or download reader still owns this task.
        with sessions() as db:
            row = db.scalar(
                select(EngineeringArchiveJob)
                .where(EngineeringArchiveJob.id == job_id)
                .with_for_update()
            )
            if not row or row.status == "running":
                return False
            if row.status != "purging":
                if row.updated_at > cutoff or row.status == "expired":
                    return False
                row.progress = {**row.progress, "cleanup_status": row.status}
                row.status = "purging"
                db.commit()
        from app.modules.engineering.archive_jobs import directory

        folder = directory(job_id)
        if folder.exists():
            with file_ownership(folder):
                remove_files(job_id)
            (folder / "worker.lock").unlink(missing_ok=True)
            folder.rmdir()
        with sessions() as db:
            row = db.scalar(
                select(EngineeringArchiveJob)
                .where(EngineeringArchiveJob.id == job_id)
                .with_for_update()
            )
            if row.status != "purging":
                raise RuntimeError("Archive cleanup state changed")
            row.status = (
                "succeeded"
                if row.operation == "import" and row.progress.get("cleanup_status") == "succeeded"
                else "expired"
            )
            row.progress = {k: v for k, v in row.progress.items() if k != "cleanup_status"}
            row.reserved_bytes = 0
            row.uploaded_bytes = 0
            row.error = None
            db.commit()
        return True


def sweep(sessions):
    cutoff = datetime.now(UTC) - timedelta(days=max(1, settings.archive_retention_days))
    with sessions() as db:
        ids = list(
            db.scalars(
                select(EngineeringArchiveJob.id)
                .where(
                    or_(
                        (EngineeringArchiveJob.status == "purging")
                        & (
                            EngineeringArchiveJob.updated_at
                            < datetime.now(UTC) - timedelta(seconds=60)
                        ),
                        (EngineeringArchiveJob.updated_at < cutoff)
                        & (
                            EngineeringArchiveJob.status.in_(
                                ("pending", "uploading", "failed", "canceled", "succeeded")
                            )
                        ),
                    )
                )
                .limit(20)
            )
        )
    for job_id in ids:
        try:
            cleanup(sessions, job_id)
        except Exception:
            logging.getLogger(__name__).exception("Archive cleanup deferred: %s", job_id)
            with sessions() as db:
                row = db.get(EngineeringArchiveJob, job_id)
                if row and row.status == "purging":
                    row.error = "暂存清理暂未完成，空间预留仍保留"
                    row.updated_at = datetime.now(UTC)
                    db.commit()
