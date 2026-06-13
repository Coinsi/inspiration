"""asset 业务逻辑:CRUD + 版本 + 参考图 + 回收站 + 影响分析。"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import Locked, NotFound
from app.kernel.graph import GraphService
from app.kernel.versioning import VersioningService
from app.models.asset import Asset, AssetReferenceImage
from app.models.consistency import VisualIdentity
from app.models.enums import AssetType, VersionStatus
from app.models.versioning import Version
from app.modules.asset import schemas
from app.platform import coding
from app.storage import cas

ENTITY = "asset"


def _snapshot(a: Asset) -> dict:
    return {
        "name": a.name,
        "summary": a.summary,
        "type": a.type if isinstance(a.type, str) else a.type.value,
        "metadata": a.meta or {},
        "tags": list(a.tags or []),
        "representative_blob_hash": a.representative_blob_hash,
    }


def _get(db: Session, project_id: uuid.UUID, asset_id: uuid.UUID, *, with_deleted=False) -> Asset:
    a = db.get(Asset, asset_id)
    if a is None or a.project_id != project_id or (not with_deleted and a.deleted_at is not None):
        raise NotFound("资产不存在")
    return a


def create(db: Session, ctx: ProjectContext, data: schemas.AssetIn) -> Asset:
    code = coding.next_code(db, ctx.project, data.type.value)
    a = Asset(
        project_id=ctx.project.id,
        code=code,
        type=data.type,
        name=data.name,
        summary=data.summary,
        meta=data.metadata,
        tags=data.tags,
        created_by=ctx.user.id,
    )
    db.add(a)
    db.flush()
    # 初始版本快照
    v = VersioningService(db).commit(
        project_id=ctx.project.id, entity_type=ENTITY, entity_id=a.id,
        content=_snapshot(a), created_by=ctx.user.id, label="初始版本",
    )
    a.current_version_id = v.id
    audit.record(db, action="asset.create", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=a.id, detail={"code": code})
    db.flush()
    return a


def update(db: Session, ctx: ProjectContext, asset_id: uuid.UUID, data: schemas.AssetUpdate) -> Asset:
    a = _get(db, ctx.project.id, asset_id)
    if a.status == VersionStatus.locked:
        raise Locked("资产已锁定,不可修改")
    if data.name is not None:
        a.name = data.name
    if data.summary is not None:
        a.summary = data.summary
    if data.metadata is not None:
        a.meta = data.metadata
    if data.tags is not None:
        a.tags = data.tags
    audit.record(db, action="asset.update", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=a.id)
    db.flush()
    return a


def list_assets(
    db: Session, project_id: uuid.UUID, *, type_: AssetType | None = None,
    status: VersionStatus | None = None, q: str | None = None, tag: str | None = None,
    deleted: bool = False, chapter_id: uuid.UUID | None = None,
) -> list[Asset]:
    stmt = select(Asset).where(Asset.project_id == project_id)
    stmt = stmt.where(Asset.deleted_at.is_not(None)) if deleted else stmt.where(Asset.deleted_at.is_(None))
    if type_ is not None:
        stmt = stmt.where(Asset.type == type_)
    if status is not None:
        stmt = stmt.where(Asset.status == status)
    if q:
        stmt = stmt.where(Asset.name.ilike(f"%{q}%"))
    if tag:
        stmt = stmt.where(Asset.tags.any(tag))
    if chapter_id is not None:
        # 章节维度:被「该章场次下的镜头」引用过的资产(场次→镜头→引用)
        from app.models.narrative import Scene
        from app.models.shot import Shot, ShotAssetRef

        sub = (
            select(ShotAssetRef.asset_id)
            .join(Shot, ShotAssetRef.shot_id == Shot.id)
            .join(Scene, Shot.scene_id == Scene.id)
            .where(Scene.adapted_from_chapter_id == chapter_id, Shot.deleted_at.is_(None))
        )
        stmt = stmt.where(Asset.id.in_(sub))
    return list(db.scalars(stmt.order_by(Asset.created_at.desc())))


def asset_counts(db: Session, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int, int]]:
    """批量取每个资产的(参考图数, 概念图数, 引用镜头数),供列表/图谱角标。"""
    from sqlalchemy import func

    from app.models.generation import Generation
    from app.models.shot import ShotAssetRef

    if not asset_ids:
        return {}
    refs = dict(
        db.execute(
            select(AssetReferenceImage.asset_id, func.count())
            .where(AssetReferenceImage.asset_id.in_(asset_ids))
            .group_by(AssetReferenceImage.asset_id)
        ).all()
    )
    gens = dict(
        db.execute(
            select(Generation.target_id, func.count())
            .where(Generation.target_type == "asset", Generation.target_id.in_(asset_ids))
            .group_by(Generation.target_id)
        ).all()
    )
    shots = dict(
        db.execute(
            select(ShotAssetRef.asset_id, func.count(func.distinct(ShotAssetRef.shot_id)))
            .where(ShotAssetRef.asset_id.in_(asset_ids))
            .group_by(ShotAssetRef.asset_id)
        ).all()
    )
    return {aid: (refs.get(aid, 0), gens.get(aid, 0), shots.get(aid, 0)) for aid in asset_ids}


def get(db: Session, project_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
    return _get(db, project_id, asset_id)


def soft_delete(db: Session, ctx: ProjectContext, asset_id: uuid.UUID) -> None:
    from sqlalchemy import func

    a = _get(db, ctx.project.id, asset_id)
    a.deleted_at = func.now()
    audit.record(db, action="asset.delete", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=a.id)
    db.flush()


def batch_soft_delete(db: Session, ctx: ProjectContext, ids: list[uuid.UUID]) -> dict:
    """批量软删除;锁定资产与不存在/已删的跳过,返回 {deleted, skipped}。"""
    from sqlalchemy import func

    deleted, skipped = 0, 0
    for aid in ids:
        a = db.get(Asset, aid)
        if a is None or a.project_id != ctx.project.id or a.deleted_at is not None:
            skipped += 1
            continue
        status = a.status.value if isinstance(a.status, VersionStatus) else a.status
        if status == "locked":
            skipped += 1
            continue
        a.deleted_at = func.now()
        deleted += 1
    audit.record(db, action="asset.batch_delete", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=None,
                 detail={"deleted": deleted, "skipped": skipped, "total": len(ids)})
    db.flush()
    return {"deleted": deleted, "skipped": skipped}


def restore(db: Session, ctx: ProjectContext, asset_id: uuid.UUID) -> Asset:
    a = _get(db, ctx.project.id, asset_id, with_deleted=True)
    a.deleted_at = None
    audit.record(db, action="asset.restore", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=a.id)
    db.flush()
    return a


def add_reference_image(
    db: Session, ctx: ProjectContext, asset_id: uuid.UUID, data: bytes, mime: str,
    role: str = "ref", note: str | None = None,
) -> AssetReferenceImage:
    a = _get(db, ctx.project.id, asset_id)
    blob = cas.put_bytes(db, data, mime)
    ref = AssetReferenceImage(asset_id=a.id, blob_hash=blob.hash, role=role, note=note)
    db.add(ref)
    if a.representative_blob_hash is None:  # 首张设为代表图
        a.representative_blob_hash = blob.hash
    db.flush()
    return ref


def list_reference_images(db: Session, project_id: uuid.UUID, asset_id: uuid.UUID) -> list[AssetReferenceImage]:
    _get(db, project_id, asset_id)
    return list(
        db.scalars(
            select(AssetReferenceImage)
            .where(AssetReferenceImage.asset_id == asset_id)
            .order_by(AssetReferenceImage.ordinal)
        )
    )


# ── 版本 ──
def commit_version(db: Session, ctx: ProjectContext, asset_id: uuid.UUID, label: str | None) -> Version:
    a = _get(db, ctx.project.id, asset_id)
    v = VersioningService(db).commit(
        project_id=ctx.project.id, entity_type=ENTITY, entity_id=a.id,
        content=_snapshot(a), created_by=ctx.user.id, label=label,
    )
    a.current_version_id = v.id
    audit.record(db, action="asset.version.commit", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=a.id, detail={"version_no": v.version_no})
    db.flush()
    return v


def list_versions(db: Session, project_id: uuid.UUID, asset_id: uuid.UUID) -> list[Version]:
    _get(db, project_id, asset_id)
    return VersioningService(db).list_versions(ENTITY, asset_id)


def rollback(db: Session, ctx: ProjectContext, asset_id: uuid.UUID, version_id: uuid.UUID) -> Version:
    a = _get(db, ctx.project.id, asset_id)
    if a.status == VersionStatus.locked:
        raise Locked("资产已锁定,不可回滚")
    vs = VersioningService(db)
    content = vs.content_of(version_id)
    # 写回 Live 行
    a.name = content.get("name", a.name)
    a.summary = content.get("summary")
    a.meta = content.get("metadata", {})
    a.tags = content.get("tags", [])
    a.representative_blob_hash = content.get("representative_blob_hash")
    # 回滚也产生新版本(可追溯)
    new_v = vs.commit(
        project_id=ctx.project.id, entity_type=ENTITY, entity_id=a.id,
        content=_snapshot(a), created_by=ctx.user.id, label=f"回滚自版本 {version_id}",
    )
    a.current_version_id = new_v.id
    audit.record(db, action="asset.rollback", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=a.id, detail={"to_version": str(version_id)})
    db.flush()
    return new_v


def lock(db: Session, ctx: ProjectContext, asset_id: uuid.UUID) -> Asset:
    a = _get(db, ctx.project.id, asset_id)
    a.status = VersionStatus.locked
    if a.current_version_id:
        VersioningService(db).lock(a.current_version_id)
    audit.record(db, action="asset.lock", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=a.id)
    db.flush()
    return a


# ── 反查 / 影响分析 ──
def usages(db: Session, project_id: uuid.UUID, asset_id: uuid.UUID) -> list[dict]:
    _get(db, project_id, asset_id)
    return GraphService(db).asset_usages(asset_id)


def impact(db: Session, project_id: uuid.UUID, asset_id: uuid.UUID) -> dict:
    _get(db, project_id, asset_id)
    return GraphService(db).asset_impact(asset_id)


# ── 视觉身份(一致性配置) ──
def set_visual_identity(db: Session, ctx: ProjectContext, asset_id: uuid.UUID, data) -> VisualIdentity:
    a = _get(db, ctx.project.id, asset_id)
    vi = db.scalar(select(VisualIdentity).where(VisualIdentity.asset_id == a.id))
    if vi is None:
        vi = VisualIdentity(asset_id=a.id)
        db.add(vi)
    vi.strategy = data.strategy
    vi.prompt_fragment_id = data.prompt_fragment_id
    vi.lora_ref = data.lora_ref
    vi.reference_set = data.reference_set
    vi.config = data.config
    audit.record(db, action="asset.visual_identity.set", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=ENTITY, target_id=a.id, detail={"strategy": data.strategy})
    db.flush()
    return vi


def get_visual_identity(db: Session, project_id: uuid.UUID, asset_id: uuid.UUID) -> VisualIdentity | None:
    _get(db, project_id, asset_id)
    return db.scalar(select(VisualIdentity).where(VisualIdentity.asset_id == asset_id))
