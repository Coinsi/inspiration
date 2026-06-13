"""generation 路由(对照 04 §3.2 / §3.6)。"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.generation import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["generation"])
TRIGGER = require_action("generation.trigger")
PROVIDER_MANAGE = require_action("provider.manage")
QUOTA_MANAGE = require_action("quota.manage")


# ── 供应商 / 配额 ──
@router.put("/providers", response_model=schemas.ProviderConfigOut)
def configure_provider(
    data: schemas.ProviderConfigIn, ctx: ProjectContext = Depends(PROVIDER_MANAGE), db: Session = Depends(get_db)
):
    return schemas.ProviderConfigOut.model_validate(service.configure_provider(db, ctx, data))


@router.get("/providers", response_model=list[schemas.ProviderConfigOut])
def list_providers(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return [schemas.ProviderConfigOut.model_validate(p) for p in service.list_providers(db, ctx.project.id)]


@router.post("/providers/test")
def test_provider(
    data: schemas.ProviderConfigIn, ctx: ProjectContext = Depends(PROVIDER_MANAGE), db: Session = Depends(get_db)
):
    ok, message = service.test_provider(
        db, ctx.project.id, data.provider_name, data.kind, data.endpoint, data.token, data.config
    )
    return {"ok": ok, "message": message}


@router.put("/quota", response_model=schemas.QuotaOut)
def set_quota(data: schemas.QuotaIn, ctx: ProjectContext = Depends(QUOTA_MANAGE), db: Session = Depends(get_db)):
    return schemas.QuotaOut.model_validate(service.set_quota(db, ctx, data))


@router.get("/quota", response_model=schemas.QuotaOut | None)
def get_quota(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    q = service.get_project_quota(db, ctx.project.id)
    return schemas.QuotaOut.model_validate(q) if q else None


# ── 生成 ──
@router.post("/shots/{shot_id}/estimate", response_model=schemas.EstimateOut)
def estimate(
    shot_id: uuid.UUID, data: schemas.GenerateIn,
    ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db),
):
    return service.estimate(db, ctx, "shot", shot_id, data)


@router.post("/shots/{shot_id}/generate", response_model=schemas.JobOut)
def generate(
    shot_id: uuid.UUID, data: schemas.GenerateIn,
    ctx: ProjectContext = Depends(TRIGGER), db: Session = Depends(get_db),
):
    return schemas.JobOut.model_validate(service.submit(db, ctx, "shot", shot_id, data))


@router.post("/assets/{asset_id}/estimate", response_model=schemas.EstimateOut)
def estimate_asset(
    asset_id: uuid.UUID, data: schemas.GenerateIn,
    ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db),
):
    return service.estimate(db, ctx, "asset", asset_id, data)


@router.post("/assets/{asset_id}/generate", response_model=schemas.JobOut)
def generate_asset(
    asset_id: uuid.UUID, data: schemas.GenerateIn,
    ctx: ProjectContext = Depends(TRIGGER), db: Session = Depends(get_db),
):
    return schemas.JobOut.model_validate(service.submit(db, ctx, "asset", asset_id, data))


@router.get("/jobs/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return schemas.JobOut.model_validate(service.get_job(db, ctx.project.id, job_id))


@router.get("/generations", response_model=list[schemas.GenerationOut])
def list_generations(
    target_type: str = Query("shot"),
    target_id: uuid.UUID = Query(...),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
    items = service.list_generations(db, ctx.project.id, target_type, target_id)
    return [schemas.GenerationOut.model_validate(g) for g in items]


@router.patch("/generations/{gen_id}", response_model=schemas.GenerationOut)
def patch_generation(
    gen_id: uuid.UUID, data: schemas.RatePatch,
    ctx: ProjectContext = Depends(TRIGGER), db: Session = Depends(get_db),
):
    return schemas.GenerationOut.model_validate(service.patch_generation(db, ctx.project.id, gen_id, data))


@router.post("/generations/{gen_id}/select", response_model=schemas.GenerationOut)
def select_variant(gen_id: uuid.UUID, ctx: ProjectContext = Depends(TRIGGER), db: Session = Depends(get_db)):
    return schemas.GenerationOut.model_validate(service.select_variant(db, ctx, gen_id))
