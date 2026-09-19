"""asset 路由(对照 04 §3.4 / §3.7;动作用子路径风格)。"""
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.models.enums import AssetType, VersionStatus
from app.modules.asset import schemas, service
from sqlalchemy.orm import Session

router = APIRouter(prefix="/projects/{project_id}", tags=["asset"])

EDIT = require_action("asset.edit")
LOCK = require_action("lock.baseline")


# ── 版本 diff(通用,放资产前避免路径歧义) ──
@router.get("/versions/diff")
def diff_versions(
    from_: uuid.UUID = Query(..., alias="from"),
    to: uuid.UUID = Query(...),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    from app.kernel.versioning import VersioningService

    return VersioningService(db).diff(from_, to)


# ── 资产 CRUD ──
@router.post("/assets", response_model=schemas.AssetOut)
def create_asset(data: schemas.AssetIn, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")):
    return schemas.AssetOut.of(service.create(db, ctx, data))


@router.get("/assets", response_model=list[schemas.AssetOut])
def list_assets(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
    type: AssetType | None = None,
    status: VersionStatus | None = None,
    q: str | None = None,
    tag: str | None = None,
    chapter_id: uuid.UUID | None = None,
):
    items = service.list_assets(db, ctx.project.id, type_=type, status=status, q=q, tag=tag, chapter_id=chapter_id)
    counts = service.asset_counts(db, [a.id for a in items])
    zero = (0, 0, 0)
    return [
        schemas.AssetOut.of(
            a,
            ref_count=counts.get(a.id, zero)[0],
            gen_count=counts.get(a.id, zero)[1],
            shot_count=counts.get(a.id, zero)[2],
        )
        for a in items
    ]


@router.get("/assets/trash", response_model=list[schemas.AssetOut])
def list_trash(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")):
    return [schemas.AssetOut.of(a) for a in service.list_assets(db, ctx.project.id, deleted=True)]


@router.get("/assets/{asset_id}", response_model=schemas.AssetOut)
def get_asset(asset_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")):
    return schemas.AssetOut.of(service.get(db, ctx.project.id, asset_id))


@router.patch("/assets/{asset_id}", response_model=schemas.AssetOut)
def update_asset(
    asset_id: uuid.UUID, data: schemas.AssetUpdate,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function"),
):
    return schemas.AssetOut.of(service.update(db, ctx, asset_id, data))


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: uuid.UUID, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")):
    service.soft_delete(db, ctx, asset_id)


@router.post("/assets/batch-delete")
def batch_delete_assets(
    data: schemas.BatchDeleteIn, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")
):
    """批量软删除(进回收站);锁定/不存在的跳过,返回 {deleted, skipped}。"""
    return service.batch_soft_delete(db, ctx, data.ids)


@router.post("/assets/{asset_id}/restore", response_model=schemas.AssetOut)
def restore_asset(asset_id: uuid.UUID, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")):
    return schemas.AssetOut.of(service.restore(db, ctx, asset_id))


@router.post("/assets/{asset_id}/lock", response_model=schemas.AssetOut)
def lock_asset(asset_id: uuid.UUID, ctx: ProjectContext = Depends(LOCK), db: Session = Depends(get_db, scope="function")):
    return schemas.AssetOut.of(service.lock(db, ctx, asset_id))


# ── 参考图 ──
@router.post("/assets/{asset_id}/reference-images", response_model=schemas.ReferenceImageOut)
def upload_reference_image(
    asset_id: uuid.UUID,
    file: UploadFile = File(...),
    role: str = Form("ref"),
    note: str | None = Form(None),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    # 同步端点:FastAPI 在线程池执行,阻塞式 MinIO I/O 不冻结事件循环
    data = file.file.read()
    ref = service.add_reference_image(
        db, ctx, asset_id, data, file.content_type or "application/octet-stream", role=role, note=note
    )
    return schemas.ReferenceImageOut.model_validate(ref)


@router.get("/assets/{asset_id}/reference-images", response_model=list[schemas.ReferenceImageOut])
def list_reference_images(
    asset_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")
):
    return [schemas.ReferenceImageOut.model_validate(r) for r in service.list_reference_images(db, ctx.project.id, asset_id)]


# ── 版本 ──
@router.post("/assets/{asset_id}/versions", response_model=schemas.VersionOut)
def commit_version(
    asset_id: uuid.UUID, data: schemas.CommitVersionIn,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function"),
):
    return schemas.VersionOut.model_validate(service.commit_version(db, ctx, asset_id, data.label))


@router.get("/assets/{asset_id}/versions", response_model=list[schemas.VersionOut])
def list_versions(asset_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")):
    return [schemas.VersionOut.model_validate(v) for v in service.list_versions(db, ctx.project.id, asset_id)]


@router.post("/assets/{asset_id}/rollback", response_model=schemas.VersionOut)
def rollback_asset(
    asset_id: uuid.UUID, version_id: uuid.UUID = Query(...),
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function"),
):
    return schemas.VersionOut.model_validate(service.rollback(db, ctx, asset_id, version_id))


# ── 反查 / 影响分析 ──
@router.get("/assets/{asset_id}/usages")
def asset_usages(asset_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")):
    return service.usages(db, ctx.project.id, asset_id)


@router.get("/assets/{asset_id}/impact")
def asset_impact(asset_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")):
    return service.impact(db, ctx.project.id, asset_id)


# ── 视觉身份(一致性) ──
@router.put("/assets/{asset_id}/visual-identity", response_model=schemas.VisualIdentityOut)
def set_visual_identity(
    asset_id: uuid.UUID, data: schemas.VisualIdentityIn,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function"),
):
    return schemas.VisualIdentityOut.model_validate(service.set_visual_identity(db, ctx, asset_id, data))


@router.get("/assets/{asset_id}/visual-identity", response_model=schemas.VisualIdentityOut | None)
def get_visual_identity(
    asset_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")
):
    vi = service.get_visual_identity(db, ctx.project.id, asset_id)
    return schemas.VisualIdentityOut.model_validate(vi) if vi else None
