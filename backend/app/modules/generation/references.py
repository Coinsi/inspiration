"""Manual generation inputs from immutable project media, independent of indexing."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_serializer, model_validator
from sqlalchemy import String, and_, cast, func, literal, or_, select, union_all

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.asset import Asset
from app.models.creation import Creation
from app.models.generation import Generation
from app.models.library import LibraryMedia, MediaVersion
from app.models.material import GeneratedMaterialState
from app.models.shot import Shot
from app.models.storage import Blob
from app.models.timeline import Timeline
from app.models.work import CreativeWork
from app.modules.generation import jobs
from app.modules.library import service as library


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_id: uuid.UUID | None = None
    media_version_id: uuid.UUID | None = None
    role: Literal["reference", "first_frame", "last_frame"] = "reference"

    @model_validator(mode="after")
    def one_source(self):
        if (self.generation_id is None) == (self.media_version_id is None):
            raise ValueError("请选择一个生成结果或固定素材版本")
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        # Preserve old persisted requests and their idempotency comparisons.
        return {k: v for k, v in handler(self).items() if v is not None}

    @property
    def source_type(self):
        return "media_version" if self.media_version_id else "generation"

    @property
    def source_id(self):
        return self.media_version_id or self.generation_id


def resolve(db, project_id, reference):
    ref = reference if isinstance(reference, Reference) else Reference.model_validate(reference)
    if ref.media_version_id:
        media, version = library.version(db, project_id, ref.media_version_id)
        if version.status != "ready" or not version.original_hash:
            raise Conflict("素材尚未完成上传处理，请就绪后再选择参考")
        blob_hash, kind = version.original_hash, version.media_type
        title, thumbnail = media.name, version.poster_hash
    else:
        item = jobs.source(db, project_id, ref.generation_id)
        jobs.validate_target(db, project_id, item.target_type, item.target_id)
        state = db.get(GeneratedMaterialState, item.id)
        if state and state.deleted_at:
            raise NotFound("素材已移入回收站")
        blob_hash, kind = item.output_blob_hash, item.output_type
        title = (state.name if state else None) or item.prompt_snapshot or "项目素材"
        thumbnail = (state.poster_hash if state else None) or item.thumbnail_hash
    if kind not in ("image", "video"):
        raise CapabilityUnsupported("请选择图片或视频素材")
    blob = db.get(Blob, blob_hash)
    if not blob or blob.size_bytes <= 0:
        raise NotFound("素材原文件不存在")
    if not blob.mime.startswith(kind + "/"):
        raise CapabilityUnsupported("素材类型与原文件格式不一致")
    return {
        "id": ref.source_id,
        "source_type": ref.source_type,
        "blob_hash": blob_hash,
        "kind": kind,
        "thumbnail_hash": thumbnail,
        "title": title[:255],
    }


def catalog(db, project_id, *, query="", media_type="image", offset=0, limit=24):
    # A pending new upload must not hide the preceding usable version. Selection
    # always stores its exact version ID; it never follows a mutable "latest" link.
    latest_ready = (
        select(MediaVersion.id)
        .where(
            MediaVersion.media_id == LibraryMedia.id,
            MediaVersion.status == "ready",
            MediaVersion.original_hash.is_not(None),
        )
        .order_by(MediaVersion.ordinal.desc())
        .limit(1)
        .correlate(LibraryMedia)
        .scalar_subquery()
    )
    uploads = (
        select(
            MediaVersion.id.label("id"),
            literal("media_version").label("source_type"),
            LibraryMedia.name.label("title"),
            MediaVersion.original_hash.label("blob_hash"),
            MediaVersion.media_type.label("kind"),
            MediaVersion.poster_hash.label("thumbnail_hash"),
            MediaVersion.created_at.label("created_at"),
            (
                LibraryMedia.name
                + " "
                + LibraryMedia.description
                + " "
                + cast(LibraryMedia.tags, String)
            ).label("search_text"),
        )
        .select_from(LibraryMedia)
        .join(MediaVersion, MediaVersion.id == latest_ready)
        .where(LibraryMedia.project_id == project_id, LibraryMedia.deleted_at.is_(None))
    )
    targets = []
    for name, model in (
        ("asset", Asset),
        ("shot", Shot),
        ("creation", Creation),
        ("timeline", Timeline),
        ("work", CreativeWork),
    ):
        conditions = [model.id == Generation.target_id, model.project_id == project_id]
        if hasattr(model, "deleted_at"):
            conditions.append(model.deleted_at.is_(None))
        targets.append(
            and_(
                Generation.target_type == name,
                select(model.id).where(*conditions).correlate(Generation).exists(),
            )
        )
    title = func.coalesce(
        GeneratedMaterialState.name, func.nullif(Generation.prompt_snapshot, ""), "项目素材"
    )
    generated = (
        select(
            Generation.id,
            literal("generation"),
            func.left(title, 255),
            Generation.output_blob_hash,
            Generation.output_type,
            func.coalesce(GeneratedMaterialState.poster_hash, Generation.thumbnail_hash),
            Generation.created_at,
            title
            + " "
            + func.coalesce(Generation.prompt_snapshot, "")
            + " "
            + func.coalesce(GeneratedMaterialState.description, "")
            + " "
            + func.coalesce(cast(GeneratedMaterialState.tags, String), ""),
        )
        .outerjoin(GeneratedMaterialState, GeneratedMaterialState.generation_id == Generation.id)
        .where(
            Generation.project_id == project_id,
            Generation.output_blob_hash.is_not(None),
            GeneratedMaterialState.deleted_at.is_(None),
            or_(*targets),
        )
    )
    rows = union_all(uploads, generated).subquery()
    kinds = ("image", "video") if media_type == "all" else (media_type,)
    stmt = (
        select(rows)
        .join(Blob, Blob.hash == rows.c.blob_hash)
        .where(
            rows.c.kind.in_(kinds),
            Blob.size_bytes > 0,
            Blob.mime.like(rows.c.kind + "/%"),
        )
    )
    if query.strip():
        stmt = stmt.where(rows.c.search_text.icontains(query.strip(), autoescape=True))
    page = list(
        db.execute(
            stmt.order_by(rows.c.created_at.desc(), rows.c.source_type, rows.c.id)
            .offset(offset)
            .limit(limit + 1)
        ).mappings()
    )
    return {
        "items": [
            {k: v for k, v in row.items() if k not in ("search_text", "created_at")}
            for row in page[:limit]
        ],
        "has_more": len(page) > limit,
    }


def validate_snapshot(db, project_id, snapshot):
    """Recheck uploaded inputs at dispatch/retry without needing an index service."""
    for source in snapshot.get("reference_sources", []):
        if not source.get("media_version_id"):
            continue  # Legacy generation references keep their existing contract.
        item = resolve(db, project_id, source)
        if not any(
            r.get("blob_hash") == item["blob_hash"]
            and r.get("kind", "image") == item["kind"]
            and r.get("role") == source.get("role", "reference")
            for r in snapshot.get("references", [])
        ):
            raise Conflict("参考素材与任务保存的版本不一致，请重新选择")
