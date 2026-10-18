"""Durable archive exports. DB locks fence workers; disk checkpoints survive restart."""

import logging
import threading
import time
from pathlib import Path

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.deps import ProjectContext
from app.core.errors import Conflict, NotFound
from app.models.engineering import EngineeringArchiveJob
from app.models.identity import Membership, Project, User
from app.models.work import CreativeWork
from app.modules.engineering import archive_capacity, archives, service
from app.modules.generation.media_engine import Canceled

log = logging.getLogger(__name__)


def directory(job_id):
    # UUID values come from the database, never user-provided filesystem paths.
    return Path(settings.fs_storage_dir).resolve() / "_engineering_exports" / str(job_id)


def output(row):
    return {
        "id": str(row.id),
        "revision": row.revision,
        "status": row.status,
        "progress": row.progress,
        "error": row.error,
        "created_at": row.created_at,
        "operation": row.operation,
        "expected_bytes": row.expected_bytes,
        "uploaded_bytes": row.uploaded_bytes,
        "filename": row.filename,
        "retention_days": settings.archive_retention_days,
        "available": row.status == "succeeded" and (directory(row.id) / "result.zip").is_file(),
    }


def create(db, ctx, work_id, request_key, revision=None):
    work = service.work(db, ctx, work_id, lock=True)
    prior = db.scalar(
        select(EngineeringArchiveJob).where(
            EngineeringArchiveJob.work_id == work_id,
            EngineeringArchiveJob.created_by == ctx.user.id,
            EngineeringArchiveJob.request_key == request_key,
        )
    )
    if prior:
        if prior.operation != "export" or (revision is not None and prior.revision != revision):
            raise Conflict("请求标识已用于其他工程版本")
        return output(prior)
    revision = work.engineering_revision if revision is None else revision
    snapshot = service.snapshot_at(db, work_id, revision)
    service.validate_sources(db, ctx, work_id, snapshot)
    row = EngineeringArchiveJob(
        work_id=work_id,
        created_by=ctx.user.id,
        request_key=request_key,
        revision=revision,
        snapshot=snapshot,
    )
    db.add(row)
    db.flush()
    archive_capacity.reserve(db, row, archive_capacity.export_size(db, row))
    return output(row)


def get(db, ctx, work_id, job_id, *, lock=False):
    service.work(db, ctx, work_id)
    query = select(EngineeringArchiveJob).where(
        EngineeringArchiveJob.id == job_id,
        EngineeringArchiveJob.work_id == work_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise NotFound("归档任务不存在")
    return row


def action(db, ctx, work_id, job_id, operation):
    row = get(db, ctx, work_id, job_id, lock=True)
    if row.created_by != ctx.user.id:
        raise Conflict("请由任务创建者管理此归档任务")
    if operation == "cancel":
        if row.status in {"uploading", "pending", "running"}:
            row.status = "canceled"
    elif operation == "retry":
        missing_result = (
            row.operation == "export"
            and row.status == "succeeded"
            and not (directory(row.id) / "result.zip").is_file()
        )
        if row.status not in {"failed", "canceled", "expired"} and not missing_result:
            raise Conflict("仅失败或已取消的任务可以重试")
        row.status, row.error = "pending", None
        if row.operation == "import" and row.uploaded_bytes < row.expected_bytes:
            row.status = "uploading"
        archive_capacity.reserve(
            db,
            row,
            row.expected_bytes + archive_capacity.OVERHEAD
            if row.operation == "import"
            else archive_capacity.export_size(db, row),
        )
    else:
        raise Conflict("不支持的归档操作")
    db.flush()
    return output(row)


def context(db, row):
    work = db.get(CreativeWork, row.work_id)
    project = db.get(Project, work.project_id) if work else None
    user = db.get(User, row.created_by)
    member = (
        db.scalar(
            select(Membership).where(
                Membership.project_id == project.id,
                Membership.user_id == row.created_by,
            )
        )
        if project
        else None
    )
    if not project or project.deleted_at is not None or not user or not member:
        raise Conflict("项目或成员权限已变更，归档任务已停止")
    ctx = ProjectContext(project=project, user=user, membership=member)
    service.work(db, ctx, row.work_id)
    if row.operation == "export":
        service.validate_sources(db, ctx, row.work_id, row.snapshot)
    else:
        from app.core.permissions import require

        require(ctx.role, "engineering.edit")
    return ctx


def execute(job_id, *, sessions=None, stop=lambda: False):
    # A dedicated transaction owns the task through execution. No long business-row
    # lock: status/cancellation stay writable. Process death releases ownership.
    sessions = sessions or SessionLocal
    key = int.from_bytes(job_id.bytes[:8], "big", signed=True)
    with sessions() as ownership:
        if not ownership.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
            return False
        with sessions() as db:
            row = db.get(EngineeringArchiveJob, job_id)
            if not row or row.status not in {"pending", "running"}:
                return False
            row.status, row.error = "running", None
            db.commit()
        last_progress = 0.0

        def canceled():
            if stop():
                return True
            # Validate lock connection still exists before touching shared staging.
            ownership.execute(text("SELECT 1"))
            with sessions() as db:
                current = db.get(EngineeringArchiveJob, job_id)
                return not current or current.status != "running"

        def progress(event):
            nonlocal last_progress
            now = time.monotonic()
            if now - last_progress < 0.5:
                return
            with sessions() as db:
                current = db.get(EngineeringArchiveJob, job_id)
                if current and current.status == "running":
                    current.progress = event
                    db.commit()
            last_progress = now

        def reserve_import(media_bytes):
            with sessions() as db:
                current = db.get(EngineeringArchiveJob, job_id)
                archive_capacity.reserve(
                    db, current, current.expected_bytes + media_bytes + archive_capacity.OVERHEAD
                )
                db.commit()

        result = None
        try:
            with sessions() as db:
                row = db.get(EngineeringArchiveJob, job_id)
                ctx = context(db, row)
                from app.modules.engineering.archive_cleanup import remove_files
                from app.modules.engineering.archive_transfer import ownership as file_ownership

                if directory(job_id).exists():
                    with file_ownership(directory(job_id)):
                        remove_files(job_id, incomplete_only=True)
                archive_capacity.reserve(
                    db,
                    row,
                    row.expected_bytes + archive_capacity.OVERHEAD
                    if row.operation == "import"
                    else archive_capacity.export_size(db, row),
                )
                db.commit()
                if row.operation == "import":
                    response = archives.restore(
                        db,
                        ctx,
                        row.work_id,
                        directory(job_id) / "upload.zip",
                        row.request_key,
                        canceled=canceled,
                        progress=progress,
                        reserve=reserve_import,
                        temp_dir=directory(job_id),
                    )
                    if canceled():
                        raise Canceled()
                    # Candidate publication and terminal task receipt share one commit.
                    row = db.scalar(
                        select(EngineeringArchiveJob)
                        .where(EngineeringArchiveJob.id == job_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if row.status != "running":
                        raise Canceled()
                    db.expire_all()
                    context(db, row)
                    row.status = "succeeded"
                    row.progress = {"phase": "complete", "change_id": str(response["id"])}
                    db.commit()
                    return True
                result = archives.build(
                    db,
                    row.work_id,
                    row.snapshot,
                    checkpoint_dir=directory(job_id),
                    canceled=canceled,
                    progress=progress,
                )
            with sessions() as db:
                row = db.scalar(
                    select(EngineeringArchiveJob)
                    .where(EngineeringArchiveJob.id == job_id)
                    .with_for_update()
                )
                if canceled():
                    raise Canceled()
                context(db, row)  # Recheck revoked permissions before publishing.
                result.replace(directory(job_id) / "result.zip")
                row.status, row.progress = "succeeded", {"phase": "complete"}
                db.commit()
            return True
        except Canceled:
            with sessions() as db:
                row = db.get(EngineeringArchiveJob, job_id)
                if row and row.status == "running":
                    row.status = "pending" if stop() else "canceled"
                    db.commit()
        except Exception:
            log.exception("Archive task failed: %s", job_id)
            try:
                ownership.execute(text("SELECT 1"))
            except Exception:
                return False  # Lost ownership must not overwrite a replacement worker.
            with sessions() as db:
                row = db.get(EngineeringArchiveJob, job_id)
                if row and row.status == "running":
                    row.status, row.error = "failed", "工程归档失败，可重试；已校验的暂存素材会复用"
                    db.commit()
        finally:
            if result:
                result.unlink(missing_ok=True)
    return False


def pump(stop=lambda: False):
    from app.modules.engineering.archive_cleanup import sweep

    sweep(SessionLocal)
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(EngineeringArchiveJob.id)
                .where(
                    EngineeringArchiveJob.status.in_(("pending", "running")),
                )
                .order_by(EngineeringArchiveJob.created_at)
                .limit(20)
            )
        )
    for job_id in ids:
        if stop():
            break
        execute(job_id, stop=stop)


def start():
    stopping = threading.Event()

    def run():
        while not stopping.is_set():
            try:
                pump(stopping.is_set)
            except Exception:
                log.exception("Archive dispatcher failed; persisted tasks remain recoverable")
            stopping.wait(3)

    thread = threading.Thread(target=run, name="engineering-archives", daemon=True)
    thread.start()

    def shutdown():
        stopping.set()
        thread.join(timeout=5)

    shutdown.is_alive = thread.is_alive
    return shutdown
