"""Ordered, idempotent upload chunks. Committed DB offset is the durable receipt."""

import os

from sqlalchemy import select

from app.core.errors import Conflict
from app.core.permissions import require
from app.models.engineering import EngineeringArchiveJob
from app.modules.engineering import archive_capacity, archive_jobs, archives, service

CHUNK_BYTES = 8 * 1024**2


def create(db, ctx, work_id, key, filename, size):
    require(ctx.role, "engineering.edit")
    work = service.work(db, ctx, work_id, lock=True)
    if not 0 < size <= archives.MAX_BYTES + 32 * 1024**2:
        raise Conflict("工程包大小超过限制")
    prior = db.scalar(
        select(EngineeringArchiveJob).where(
            EngineeringArchiveJob.work_id == work_id,
            EngineeringArchiveJob.created_by == ctx.user.id,
            EngineeringArchiveJob.request_key == key,
        )
    )
    if prior:
        if (
            prior.operation != "import"
            or prior.expected_bytes != size
            or prior.filename != filename
        ):
            raise Conflict("请求标识已用于其他上传")
        return archive_jobs.output(prior)
    if work.engineering_revision:
        raise Conflict("请将完整归档导入空作品")
    row = EngineeringArchiveJob(
        work_id=work_id,
        created_by=ctx.user.id,
        request_key=key,
        operation="import",
        status="uploading",
        revision=0,
        snapshot={},
        expected_bytes=size,
        uploaded_bytes=0,
        filename=filename,
    )
    db.add(row)
    db.flush()
    archive_capacity.reserve(db, row, size + archive_capacity.OVERHEAD)
    return archive_jobs.output(row)


def owned(db, ctx, work_id, job_id):
    require(ctx.role, "engineering.edit")
    row = archive_jobs.get(db, ctx, work_id, job_id, lock=True)
    if row.operation != "import" or row.created_by != ctx.user.id:
        raise Conflict("无权修改此上传任务")
    return row


def append(db, ctx, work_id, job_id, offset, data):
    row = owned(db, ctx, work_id, job_id)
    if row.status != "uploading":
        raise Conflict("上传会话不在接收状态")
    if not data or len(data) > CHUNK_BYTES or offset < 0 or offset + len(data) > row.expected_bytes:
        raise Conflict("上传分片大小或位置不合法")
    folder = archive_jobs.directory(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "upload.zip"
    if offset < row.uploaded_bytes:
        if offset + len(data) > row.uploaded_bytes or not path.exists():
            raise Conflict("分片与已确认位置不一致")
        with path.open("rb") as stream:
            stream.seek(offset)
            if stream.read(len(data)) != data:
                raise Conflict("重复分片内容不一致，请重新选择原文件")
        return archive_jobs.output(row)
    if offset != row.uploaded_bytes:
        raise Conflict("分片顺序不一致，请刷新上传进度")
    with path.open("r+b" if path.exists() else "w+b") as target:
        target.seek(0, 2)
        if target.tell() < row.uploaded_bytes:
            raise Conflict("已确认的暂存文件缺失，请重新创建上传")
        # A process may die after fsync but before committing the receipt.
        target.truncate(row.uploaded_bytes)
        target.seek(row.uploaded_bytes)
        target.write(data)
        target.flush()
        os.fsync(target.fileno())
    row.uploaded_bytes += len(data)
    row.progress = {
        "phase": "uploading",
        "completed_bytes": row.uploaded_bytes,
        "total_bytes": row.expected_bytes,
    }
    db.flush()
    return archive_jobs.output(row)


def finish(db, ctx, work_id, job_id):
    row = owned(db, ctx, work_id, job_id)
    if row.status in {"pending", "running", "succeeded"}:
        return archive_jobs.output(row)
    path = archive_jobs.directory(job_id) / "upload.zip"
    if (
        row.status != "uploading"
        or row.uploaded_bytes != row.expected_bytes
        or not path.is_file()
        or path.stat().st_size != row.expected_bytes
    ):
        raise Conflict("工程包尚未完整上传")
    row.status = "pending"
    db.flush()
    return archive_jobs.output(row)
