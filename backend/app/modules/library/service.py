"""Resumable uploads and version-pinned references. All mutations lock the media root."""

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.core import audit
from app.core.config import settings
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.library import LibraryMedia, MediaUsage, MediaVersion
from app.models.media_index import MediaIndex
from app.models.shot import Shot

CHUNK_SIZE = 8 * 1024 * 1024
ACTIVE = ("uploading", "queued", "processing")


def staging_path(version_id):
    # Only a parsed server UUID is used as a path segment, never an uploaded filename.
    base = Path(settings.library_staging_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{uuid.UUID(str(version_id))}.upload"


def media(db, project_id, media_id, *, lock=False, include_deleted=False):
    stmt = select(LibraryMedia).where(
        LibraryMedia.id == media_id, LibraryMedia.project_id == project_id
    )
    item = db.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt
    )
    if not item or (item.deleted_at and not include_deleted):
        raise NotFound("视频素材不存在")
    return item


def version(db, project_id, version_id, *, lock=False):
    v = db.get(MediaVersion, version_id)
    if not v:
        raise NotFound("视频版本不存在")
    item = media(db, project_id, v.media_id, lock=lock)
    if lock:
        db.refresh(v)
    return item, v


def version_out(v):
    fields = (
        "id",
        "media_id",
        "ordinal",
        "filename",
        "size_bytes",
        "uploaded_bytes",
        "fingerprint",
        "status",
        "error",
        "original_hash",
        "proxy_hash",
        "poster_hash",
        "duration_ms",
        "width",
        "height",
        "created_at",
    )
    return {**{key: getattr(v, key) for key in fields}, "chunk_size": CHUNK_SIZE}


def list_media(db, project_id, deleted=False):
    items = list(
        db.scalars(
            select(LibraryMedia)
            .where(
                LibraryMedia.project_id == project_id,
                LibraryMedia.deleted_at.is_not(None)
                if deleted
                else LibraryMedia.deleted_at.is_(None),
            )
            .order_by(LibraryMedia.created_at.desc())
        )
    )
    return media_entries(db, project_id, items)


def catalog(
    db,
    project_id,
    deleted=False,
    query="",
    offset=0,
    limit=24,
    sort="recent",
    folder_id=None,
    root_only=False,
):
    from app.modules.library import folders

    folders.get(db, project_id, folder_id)
    conditions = [
        LibraryMedia.project_id == project_id,
        LibraryMedia.deleted_at.is_not(None) if deleted else LibraryMedia.deleted_at.is_(None),
    ]
    if query.strip():
        conditions.append(LibraryMedia.name.icontains(query.strip(), autoescape=True))
    if folder_id is not None or root_only:
        conditions.append(LibraryMedia.folder_id == folder_id)
    total = db.scalar(select(func.count()).select_from(LibraryMedia).where(*conditions))
    order = {
        "recent": LibraryMedia.created_at.desc(),
        "oldest": LibraryMedia.created_at.asc(),
        "name": LibraryMedia.name.asc(),
    }[sort]
    items = list(
        db.scalars(
            select(LibraryMedia)
            .where(*conditions)
            .order_by(order, LibraryMedia.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "items": media_entries(db, project_id, items),
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def version_catalog(db, project_id, query="", offset=0, limit=24):
    """Bound the actual selectable versions, including media with many revisions."""
    conditions = [
        LibraryMedia.project_id == project_id,
        LibraryMedia.deleted_at.is_(None),
        MediaVersion.status == "ready",
    ]
    if query.strip():
        conditions.append(LibraryMedia.name.icontains(query.strip(), autoescape=True))
    total = db.scalar(
        select(func.count()).select_from(MediaVersion).join(LibraryMedia).where(*conditions)
    )
    rows = db.execute(
        select(MediaVersion, LibraryMedia.name)
        .join(LibraryMedia)
        .where(*conditions)
        .order_by(LibraryMedia.created_at.desc(), LibraryMedia.id, MediaVersion.ordinal.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [{**version_out(v), "name": name} for v, name in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def media_entries(db, project_id, items):
    from app.modules.library.reuse import origins

    if not items:
        return []
    ids = [item.id for item in items]
    versions = list(
        db.scalars(
            select(MediaVersion)
            .join(LibraryMedia)
            .where(LibraryMedia.project_id == project_id, MediaVersion.media_id.in_(ids))
            .order_by(MediaVersion.ordinal.desc())
        )
    )
    counts = dict(
        db.execute(
            select(MediaVersion.media_id, func.count(MediaUsage.id))
            .join(MediaUsage)
            .join(LibraryMedia)
            .where(LibraryMedia.project_id == project_id, MediaVersion.media_id.in_(ids))
            .group_by(MediaVersion.media_id)
        ).all()
    )
    source = origins(db, [v.id for v in versions])
    grouped = {}
    for v in versions:
        grouped.setdefault(v.media_id, []).append(
            {**version_out(v), "reuse_origin": source.get(v.id)}
        )
    return [
        {
            "id": i.id,
            "name": i.name,
            "folder_id": i.folder_id,
            "deleted_at": i.deleted_at,
            "versions": grouped.get(i.id, []),
            "usage_count": counts.get(i.id, 0),
        }
        for i in items
    ]


def begin_upload(db, ctx, data):
    from app.modules.library import folders

    if data.size_bytes > settings.library_max_upload_bytes:
        raise CapabilityUnsupported(
            f"单个视频最多 {settings.library_max_upload_bytes // (1024**3)} GB"
        )
    if Path(data.filename).suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
        raise CapabilityUnsupported("请选择 MP4、MOV、MKV 或 WebM 视频")
    if data.media_id:
        item = media(db, ctx.project.id, data.media_id, lock=True)
        if db.scalar(
            select(MediaVersion.id).where(
                MediaVersion.media_id == item.id, MediaVersion.status.in_(ACTIVE)
            )
        ):
            raise Conflict("该素材已有上传或处理任务，请先完成或取消")
    else:
        if data.folder_id:
            folders.lock_project(db, ctx.project.id)
            folders.get(db, ctx.project.id, data.folder_id)
        item = LibraryMedia(
            project_id=ctx.project.id, name=data.filename.strip(), folder_id=data.folder_id
        )
        db.add(item)
        db.flush()
    ordinal = (
        db.scalar(select(func.max(MediaVersion.ordinal)).where(MediaVersion.media_id == item.id))
        or 0
    ) + 1
    v = MediaVersion(
        media_id=item.id,
        ordinal=ordinal,
        filename=data.filename,
        size_bytes=data.size_bytes,
        fingerprint=data.fingerprint,
    )
    db.add(v)
    db.flush()
    audit.record(
        db,
        action="library.upload.begin",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=item.id,
        detail={"version_id": str(v.id), "size_bytes": v.size_bytes},
    )
    return version_out(v)


def append_chunk(db, ctx, version_id, offset, upload):
    _, v = version(db, ctx.project.id, version_id, lock=True)
    if v.status != "uploading":
        raise Conflict("该上传已结束或已取消")
    if offset != v.uploaded_bytes:
        raise Conflict("上传位置已变化，请同步进度后继续", {"uploaded_bytes": v.uploaded_bytes})
    data = upload.file.read(CHUNK_SIZE + 1)
    if len(data) != min(CHUNK_SIZE, v.size_bytes - offset) or not data:
        raise CapabilityUnsupported("分块大小或上传范围无效")
    path = staging_path(v.id)
    if offset and (not path.exists() or path.stat().st_size < offset):
        raise Conflict("服务器暂存文件缺失，请取消后重新上传")
    with path.open("r+b" if path.exists() else "w+b") as stream:
        # Discard uncommitted tail after a failed DB transaction; the DB offset is authoritative.
        stream.truncate(offset)
        stream.seek(offset)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    v.uploaded_bytes += len(data)
    v.chunk_hashes = [*v.chunk_hashes, hashlib.sha256(data).hexdigest()]
    db.flush()
    return version_out(v)


def complete_upload(db, ctx, version_id):
    _, v = version(db, ctx.project.id, version_id, lock=True)
    if v.status in ("queued", "processing", "ready"):
        return version_out(v)
    if v.status != "uploading" or v.uploaded_bytes != v.size_bytes:
        raise Conflict("视频尚未上传完成")
    path = staging_path(v.id)
    if not path.exists() or path.stat().st_size != v.size_bytes:
        raise Conflict("暂存文件不完整，请重新上传")
    v.status, v.error = "queued", None
    db.flush()
    return version_out(v)


def retry(db, ctx, version_id):
    _, v = version(db, ctx.project.id, version_id, lock=True)
    if v.status != "failed":
        raise Conflict("仅失败任务可重试")
    if not v.original_hash and not staging_path(v.id).is_file():
        raise Conflict("源文件不可用，请重新上传")
    v.status, v.error = "queued", None
    db.flush()
    return version_out(v)


def cancel_upload(db, ctx, version_id):
    _, v = version(db, ctx.project.id, version_id, lock=True)
    if v.status not in ("uploading", "failed", "canceled"):
        raise Conflict("处理中的素材不能取消，请等待任务结束")
    v.status = "canceled"
    db.commit()
    staging_path(v.id).unlink(missing_ok=True)


def usage_out(db, u):
    v = db.get(MediaVersion, u.version_id)
    item = db.get(LibraryMedia, v.media_id)
    shot = db.get(Shot, u.shot_id)
    return {
        "id": u.id,
        "version_id": v.id,
        "media_id": item.id,
        "name": item.name,
        "ordinal": v.ordinal,
        "shot_id": u.shot_id,
        "shot_title": shot.title or shot.code,
        "shot_deleted": shot.deleted_at is not None,
        "start_ms": u.start_ms,
        "end_ms": u.end_ms,
        "purpose": u.purpose,
        "note": u.note,
        "proxy_hash": v.proxy_hash,
        "original_hash": v.original_hash,
    }


def usages(db, project_id, media_id=None, shot_id=None):
    query = (
        select(MediaUsage)
        .join(MediaVersion)
        .join(LibraryMedia)
        .where(LibraryMedia.project_id == project_id)
    )
    if media_id:
        query = query.where(LibraryMedia.id == media_id)
    if shot_id:
        query = query.where(MediaUsage.shot_id == shot_id)
    return [usage_out(db, u) for u in db.scalars(query.order_by(MediaUsage.created_at))]


def create_usage(db, ctx, data):
    item, v = version(db, ctx.project.id, data.version_id, lock=True)
    # Same root lock as trash/new-version operations. A version never follows latest implicitly.
    shot = db.scalar(select(Shot).where(Shot.id == data.shot_id).with_for_update())
    if not shot or shot.project_id != ctx.project.id or shot.deleted_at:
        raise NotFound("目标镜头不存在")
    if v.status != "ready" or not v.duration_ms:
        raise Conflict("请等待视频处理完成")
    if data.end_ms - data.start_ms < 100 or data.end_ms > v.duration_ms:
        raise CapabilityUnsupported("片段至少0.1秒，且必须在视频时长范围内")
    existing = db.scalar(
        select(MediaUsage).where(
            MediaUsage.version_id == v.id,
            MediaUsage.shot_id == shot.id,
            MediaUsage.start_ms == data.start_ms,
            MediaUsage.end_ms == data.end_ms,
            MediaUsage.purpose == data.purpose,
        )
    )
    if existing:
        return usage_out(db, existing)
    u = MediaUsage(**data.model_dump())
    db.add(u)
    db.flush()
    audit.record(
        db,
        action="library.reference.create",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=item.id,
        detail={"usage_id": str(u.id), **data.model_dump(mode="json")},
    )
    return usage_out(db, u)


def remove_usage(db, ctx, usage_id):
    u = db.get(MediaUsage, usage_id)
    if not u:
        raise NotFound("引用不存在")
    item, _ = version(db, ctx.project.id, u.version_id, lock=True)
    db.delete(u)
    audit.record(
        db,
        action="library.reference.remove",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=item.id,
        detail={"usage_id": str(usage_id)},
    )


def impact(db, project_id, media_id):
    from app.models.evidence import IdentityEvidence
    from app.models.generation import GenerationJob
    from app.models.transcription import TranscriptionRun
    from app.modules.library.reuse import retained_count

    evidence_count = db.scalar(
        select(func.count())
        .select_from(IdentityEvidence)
        .join(MediaVersion)
        .where(
            MediaVersion.media_id == media_id,
            IdentityEvidence.project_id == project_id,
            IdentityEvidence.status.in_(["proposed", "confirmed"]),
        )
    )
    refs = usages(db, project_id, media_id=media_id)
    active = list(
        db.scalars(
            select(MediaVersion.id).where(
                MediaVersion.media_id == media_id, MediaVersion.status.in_(ACTIVE)
            )
        )
    )
    active.extend(
        db.scalars(
            select(MediaIndex.id)
            .join(MediaVersion)
            .where(
                MediaVersion.media_id == media_id, MediaIndex.status.in_(("queued", "processing"))
            )
        )
    )
    active.extend(
        db.scalars(
            select(TranscriptionRun.id)
            .join(MediaVersion)
            .where(
                MediaVersion.media_id == media_id,
                TranscriptionRun.status.in_(["queued", "processing"]),
            )
        )
    )
    active.extend(
        db.scalars(
            select(GenerationJob.id).where(
                GenerationJob.project_id == project_id,
                GenerationJob.status.in_(("pending", "submitted", "running")),
                GenerationJob.input_snapshot["library_origin"]["media_id"].astext == str(media_id),
            )
        )
    )
    shared_count = retained_count(db, media_id)
    return {
        "references": refs,
        "active_versions": active,
        "evidence_count": evidence_count,
        "shared_count": shared_count,
        "can_trash": not refs and not active and not evidence_count and not shared_count,
        "physical_delete": False,
    }


def trash(db, ctx, media_id, restore=False):
    item = media(db, ctx.project.id, media_id, lock=True, include_deleted=True)
    info = impact(db, ctx.project.id, media_id)
    if not restore and not info["can_trash"]:
        raise Conflict(
            "仍有镜头引用、跨项目复用、身份判断证据或上传/处理任务，无法移入回收站",
            {
                "reference_count": len(info["references"]),
                "active_count": len(info["active_versions"]),
                "evidence_count": info["evidence_count"],
                "shared_count": info["shared_count"],
            },
        )
    item.deleted_at = None if restore else datetime.now(UTC)
    audit.record(
        db,
        action="library.restore" if restore else "library.trash",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=item.id,
    )
    return {"restored": restore, "physical_delete": False}
