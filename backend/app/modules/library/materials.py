"""One paged media catalog over existing immutable sources, without file copies."""

from datetime import UTC, datetime

from sqlalchemy import String, and_, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import aliased

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.asset import Asset
from app.models.generation import Generation, GenerationJob
from app.models.library import LibraryMedia, MediaVersion
from app.models.material import GeneratedMaterialState
from app.models.shot import Shot
from app.models.storage import Blob
from app.modules.library import generated, service


def catalog(db, project_id, *, query="", kind=None, source=None, deleted=False, offset=0, limit=24):
    variant = aliased(Generation)
    adopted = (
        select(variant.id)
        .where(
            variant.project_id == project_id,
            or_(
                variant.id == Generation.id,
                variant.input_refs["adoption_source_id"].astext == cast(Generation.id, String),
            ),
            or_(
                and_(
                    variant.target_type == "shot",
                    select(Shot.id)
                    .where(
                        Shot.id == variant.target_id,
                        Shot.deleted_at.is_(None),
                        Shot.selected_generation_id == variant.id,
                    )
                    .exists(),
                ),
                and_(
                    variant.target_type == "asset",
                    variant.is_selected.is_(True),
                    select(Asset.id)
                    .where(
                        Asset.id == variant.target_id,
                        Asset.deleted_at.is_(None),
                        Asset.representative_blob_hash == variant.output_blob_hash,
                    )
                    .exists(),
                ),
            ),
        )
        .correlate(Generation)
        .exists()
    )
    latest = (
        select(MediaVersion.id)
        .where(MediaVersion.media_id == LibraryMedia.id)
        .order_by(MediaVersion.ordinal.desc())
        .limit(1)
        .correlate(LibraryMedia)
        .scalar_subquery()
    )
    uploads = (
        select(
            LibraryMedia.id.label("id"),
            literal("upload").label("source"),
            LibraryMedia.name.label("name"),
            LibraryMedia.description.label("description"),
            LibraryMedia.tags.label("tags"),
            LibraryMedia.metadata_revision.label("metadata_revision"),
            MediaVersion.media_type.label("kind"),
            LibraryMedia.created_at.label("created_at"),
            LibraryMedia.deleted_at.label("deleted_at"),
            MediaVersion.status.label("status"),
            MediaVersion.poster_hash.label("poster_hash"),
            MediaVersion.original_hash.label("blob_hash"),
            MediaVersion.proxy_hash.label("preview_hash"),
            MediaVersion.size_bytes.label("size_bytes"),
            MediaVersion.duration_ms.label("duration_ms"),
            cast(MediaVersion.id, String).label("version_id"),
            (LibraryMedia.description + " " + cast(LibraryMedia.tags, String)).label(
                "search_extra"
            ),
            literal(False).label("is_test"),
            literal(False).label("adopted"),
        )
        .select_from(LibraryMedia)
        .outerjoin(MediaVersion, MediaVersion.id == latest)
        .where(LibraryMedia.project_id == project_id)
    )
    outputs = (
        select(
            Generation.id,
            literal("generated"),
            func.coalesce(
                GeneratedMaterialState.name,
                func.left(
                    func.coalesce(func.nullif(Generation.prompt_snapshot, ""), "AI 生成素材"), 120
                ),
            ),
            func.coalesce(GeneratedMaterialState.description, ""),
            GeneratedMaterialState.tags,
            func.coalesce(GeneratedMaterialState.metadata_revision, 1),
            Generation.output_type,
            Generation.created_at,
            GeneratedMaterialState.deleted_at,
            literal("ready"),
            GeneratedMaterialState.poster_hash,
            Blob.hash,
            Blob.hash,
            Blob.size_bytes,
            Blob.duration_ms,
            literal(None, type_=String),
            func.coalesce(Generation.prompt_snapshot, "")
            + " "
            + func.coalesce(Generation.model, "")
            + " "
            + func.coalesce(GeneratedMaterialState.description, "")
            + " "
            + func.coalesce(cast(GeneratedMaterialState.tags, String), ""),
            (Generation.provider == "mock"),
            adopted,
        )
        .select_from(Generation)
        .join(GenerationJob, Generation.job_id == GenerationJob.id)
        .join(Blob, Generation.output_blob_hash == Blob.hash)
        .outerjoin(GeneratedMaterialState, GeneratedMaterialState.generation_id == Generation.id)
        .where(
            Generation.project_id == project_id,
            GenerationJob.project_id == project_id,
            GenerationJob.status == "succeeded",
            Generation.output_type.in_(("image", "video")),
            Generation.provider.not_in(("upload", "local", "library")),
        )
    )
    # Entity rows belong to the shared trash, not the physical media grid.
    entities = select(
        Asset.id,
        literal("asset"),
        Asset.name,
        literal(""),
        literal(None),
        literal(1),
        literal("entity"),
        Asset.created_at,
        Asset.deleted_at,
        literal("ready"),
        literal(None, type_=String),
        literal(None, type_=String),
        literal(None, type_=String),
        literal(0),
        literal(None),
        literal(None, type_=String),
        Asset.code,
        literal(False),
        literal(False),
    ).where(Asset.project_id == project_id, Asset.deleted_at.is_not(None))
    parts = [uploads, outputs] + ([entities] if deleted else [])
    rows = union_all(*parts).subquery()
    conditions = [rows.c.deleted_at.is_not(None) if deleted else rows.c.deleted_at.is_(None)]
    if query.strip():
        conditions.append(
            (rows.c.name + " " + rows.c.search_extra).icontains(query.strip(), autoescape=True)
        )
    if source:
        conditions.append(rows.c.source == source)
    if kind:
        conditions.append(rows.c.kind == kind)
    stmt = select(rows).where(*conditions)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    result = db.execute(
        stmt.order_by(rows.c.created_at.desc(), rows.c.source, rows.c.id)
        .offset(offset)
        .limit(limit)
    ).mappings()
    return {
        "items": [{k: v for k, v in dict(r).items() if k != "search_extra"} for r in result],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def state(db, ctx, source, item_id, *, restore=False):
    if source == "upload":
        return service.trash(db, ctx, item_id, restore=restore)
    if source == "asset":
        from app.modules.asset import service as assets

        if restore:
            assets.restore(db, ctx, item_id)
        else:
            assets.soft_delete(db, ctx, item_id)
        return {"restored": restore, "physical_delete": False}
    row = db.execute(
        generated.query_for(ctx.project.id)
        .where(Generation.id == item_id)
        .with_for_update(of=Generation)
    ).first()
    if not row:
        raise NotFound("生成素材不存在")
    record = db.get(GeneratedMaterialState, item_id)
    if not record:
        record = GeneratedMaterialState(generation_id=item_id)
        db.add(record)
    record.deleted_at = None if restore else datetime.now(UTC)
    audit.record(
        db,
        action="material.restore" if restore else "material.trash",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=item_id,
        detail={"source": source, "physical_delete": False},
    )
    db.flush()
    return {"restored": restore, "physical_delete": False}


def update_profile(db, ctx, source, item_id, data, file=None):
    from app.modules.identity.covers import read_cover, store

    cover = read_cover(file)
    version = None
    if source == "upload":
        record = service.media(db, ctx.project.id, item_id, lock=True)
        if file:
            version = db.scalar(
                select(MediaVersion)
                .where(MediaVersion.id == data.version_id, MediaVersion.media_id == record.id)
                .with_for_update()
            )
            if not version or version.media_type != "video" or version.status != "ready":
                raise CapabilityUnsupported("仅能为已就绪的视频版本设置封面")
    else:
        row = db.execute(
            generated.query_for(ctx.project.id)
            .where(Generation.id == item_id)
            .with_for_update(of=Generation)
        ).first()
        if not row:
            raise NotFound("生成素材不存在")
        record = db.get(GeneratedMaterialState, item_id)
        if record and record.deleted_at:
            raise NotFound("素材已移入回收站")
        if file and row[0].output_type != "video":
            raise CapabilityUnsupported("仅视频素材支持选取封面")
        if not record:
            record = GeneratedMaterialState(generation_id=item_id, metadata_revision=1)
            db.add(record)
    if record.metadata_revision != data.revision:
        raise Conflict("素材资料已被修改，请关闭后重新打开编辑")
    record.name = data.name
    record.description = data.description
    record.tags = data.tags
    record.metadata_revision += 1
    if file:
        digest = store(db, cover)
        if version:
            version.poster_hash = digest
        else:
            record.poster_hash = digest
    audit.record(
        db,
        action="material.update",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=item_id,
        detail={"source": source, "name": data.name, "cover_changed": file is not None},
    )
    db.flush()
    return {
        "name": record.name,
        "description": record.description,
        "tags": record.tags,
        "metadata_revision": record.metadata_revision,
    }
