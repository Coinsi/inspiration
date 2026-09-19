"""Cross-project reuse grants a fixed media version to the destination project."""

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.core import audit
from app.core.deps import get_project_context
from app.core.errors import Conflict
from app.core.permissions import can, require
from app.models.enums import Role
from app.models.identity import Membership, Project
from app.models.library import LibraryMedia, MediaReuse, MediaVersion
from app.modules.library import service


def projects(db, ctx):
    rows = db.execute(
        select(Project, Membership.role)
        .join(Membership, Membership.project_id == Project.id)
        .where(
            Membership.user_id == ctx.user.id,
            Project.deleted_at.is_(None),
            Project.id != ctx.project.id,
        )
        .order_by(Project.name, Project.id)
    ).all()
    return [{"id": p.id, "name": p.name} for p, role in rows if can(Role(role), "asset.edit")]


def create(db, ctx, data):
    from app.modules.library import folders

    if data.source_project_id == ctx.project.id:
        raise Conflict("同一项目直接使用已有版本，无需跨项目复用")
    # A deterministic project order serializes duplicate/reciprocal reuse requests.
    db.execute(
        select(Project)
        .where(Project.id.in_([ctx.project.id, data.source_project_id]))
        .order_by(Project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    ctx = get_project_context(ctx.project.id, ctx.user, db)
    source_ctx = get_project_context(data.source_project_id, ctx.user, db)
    require(source_ctx.role, "asset.edit")
    require(ctx.role, "asset.edit")
    folders.get(db, ctx.project.id, data.folder_id)
    item, source = service.version(db, source_ctx.project.id, data.source_version_id, lock=True)
    if source.status != "ready" or not source.original_hash or not source.proxy_hash:
        raise Conflict("请先完成原视频上传和预览处理")
    existing = db.scalar(
        select(MediaReuse).where(
            MediaReuse.source_version_id == source.id,
            MediaReuse.target_project_id == ctx.project.id,
        )
    )
    if existing:
        target = db.get(MediaVersion, existing.target_version_id)
        target_item = service.media(
            db, ctx.project.id, target.media_id, lock=True, include_deleted=True
        )
        if target_item.deleted_at:
            raise Conflict(
                "此版本已在本项目回收站，请先恢复已有素材", {"media_id": str(target_item.id)}
            )
        return {"media_id": target_item.id, "version_id": target.id, "reused": True}
    target_item = LibraryMedia(project_id=ctx.project.id, name=item.name, folder_id=data.folder_id)
    db.add(target_item)
    db.flush()
    target = MediaVersion(
        media_id=target_item.id,
        ordinal=1,
        status="ready",
        filename=source.filename,
        size_bytes=source.size_bytes,
        uploaded_bytes=source.size_bytes,
        fingerprint=source.fingerprint,
        original_hash=source.original_hash,
        proxy_hash=source.proxy_hash,
        poster_hash=source.poster_hash,
        duration_ms=source.duration_ms,
        width=source.width,
        height=source.height,
    )
    db.add(target)
    db.flush()
    db.add(
        MediaReuse(
            source_version_id=source.id,
            target_version_id=target.id,
            target_project_id=ctx.project.id,
            created_by=ctx.user.id,
            source_name=item.name,
            source_ordinal=source.ordinal,
        )
    )
    audit.record(
        db,
        action="library.reuse",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="media_version",
        target_id=target.id,
        detail={
            "source_version_id": str(source.id),
            "source_project_id": str(source_ctx.project.id),
        },
    )
    audit.record(
        db,
        action="library.reuse.grant",
        user_id=ctx.user.id,
        project_id=source_ctx.project.id,
        target_type="media_version",
        target_id=source.id,
        detail={"target_project_id": str(ctx.project.id), "target_version_id": str(target.id)},
    )
    db.flush()
    return {"media_id": target_item.id, "version_id": target.id, "reused": False}


def origins(db, version_ids):
    if not version_ids:
        return {}
    return {
        r.target_version_id: {
            "version_id": r.source_version_id,
            "name": r.source_name,
            "ordinal": r.source_ordinal,
        }
        for r in db.scalars(select(MediaReuse).where(MediaReuse.target_version_id.in_(version_ids)))
    }


def retained_count(db, media_id):
    source = aliased(MediaVersion)
    target = aliased(MediaVersion)
    return db.scalar(
        select(func.count())
        .select_from(MediaReuse)
        .join(source, source.id == MediaReuse.source_version_id)
        .join(target, target.id == MediaReuse.target_version_id)
        .join(LibraryMedia, LibraryMedia.id == target.media_id)
        .where(source.media_id == media_id, LibraryMedia.deleted_at.is_(None))
    )


def destinations(db, user_id, media_id):
    source, target = aliased(MediaVersion), aliased(MediaVersion)
    rows = db.execute(
        select(MediaReuse, LibraryMedia, Project, Membership.id)
        .join(source, source.id == MediaReuse.source_version_id)
        .join(target, target.id == MediaReuse.target_version_id)
        .join(LibraryMedia, LibraryMedia.id == target.media_id)
        .join(Project, Project.id == LibraryMedia.project_id)
        .outerjoin(
            Membership, (Membership.project_id == Project.id) & (Membership.user_id == user_id)
        )
        .where(source.media_id == media_id, LibraryMedia.deleted_at.is_(None))
        .order_by(MediaReuse.created_at, MediaReuse.id)
        .limit(100)
    ).all()
    return [
        {
            "project_id": project.id if member and not project.deleted_at else None,
            "project_name": project.name if member and not project.deleted_at else None,
            "media_id": item.id if member and not project.deleted_at else None,
            "version_id": link.target_version_id if member and not project.deleted_at else None,
            "source_ordinal": link.source_ordinal,
        }
        for link, item, project, member in rows
    ]
