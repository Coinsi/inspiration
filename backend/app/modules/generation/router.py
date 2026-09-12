"""generation 路由(对照 04 §3.2 / §3.6)。"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.models.generation import GenerationJob
from app.modules.generation import jobs, schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["generation"])
TRIGGER = require_action("generation.trigger")
PROVIDER_MANAGE = require_action("provider.manage")
QUOTA_MANAGE = require_action("quota.manage")


@router.post("/generations/{gen_id}/inpaint", response_model=schemas.JobOut)
def inpaint(
    gen_id: uuid.UUID,
    data: schemas.InpaintIn,
    ctx: ProjectContext = Depends(TRIGGER),
    db: Session = Depends(get_db),
):
    from app.modules.generation.editing import inpaint as submit_inpaint

    return submit_inpaint(db, ctx, gen_id, data)


@router.post("/prompts/optimize", response_model=schemas.OptimizedPrompt)
def optimize_prompt(
    data: schemas.OptimizePromptIn,
    ctx: ProjectContext = Depends(TRIGGER),
    db: Session = Depends(get_db),
):
    from app.modules.generation.editing import optimize_prompt as optimize

    return optimize(db, ctx, data)


@router.get("/providers/{name}/capabilities")
def capabilities(
    name: str, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)
):
    return (
        service._provider_instance(db, ctx.project.id, name).capabilities().model_dump(mode="json")
    )


@router.get("/jobs", response_model=list[schemas.JobOut])
def list_jobs(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
    q = select(GenerationJob).where(GenerationJob.project_id == ctx.project.id)
    if status:
        q = q.where(GenerationJob.status == status)
    return list(db.scalars(q.order_by(GenerationJob.created_at.desc()).limit(limit).offset(offset)))


@router.post("/jobs/{job_id}/cancel", response_model=schemas.JobOut)
def cancel_job(
    job_id: uuid.UUID, ctx: ProjectContext = Depends(TRIGGER), db: Session = Depends(get_db)
):
    return jobs.cancel(db, ctx, job_id)


@router.post("/jobs/{job_id}/retry", response_model=schemas.JobOut)
def retry_job(
    job_id: uuid.UUID, ctx: ProjectContext = Depends(TRIGGER), db: Session = Depends(get_db)
):
    return jobs.retry(db, ctx, job_id)


@router.post("/generations/{gen_id}/refine", response_model=schemas.JobOut)
def refine(
    gen_id: uuid.UUID,
    data: schemas.RefineIn,
    ctx: ProjectContext = Depends(TRIGGER),
    db: Session = Depends(get_db),
):
    return jobs.refine(db, ctx, gen_id, data)


@router.post("/timelines/{timeline_id}/render", response_model=schemas.JobOut)
def render(
    timeline_id: uuid.UUID,
    data: schemas.RenderIn,
    ctx: ProjectContext = Depends(require_action("timeline.edit")),
    db: Session = Depends(get_db),
):
    return jobs.render_timeline(db, ctx, timeline_id, data)


@router.post("/media/{target_type}/{target_id}/upload", response_model=schemas.GenerationOut)
def upload_media(
    target_type: Literal["shot", "asset"],
    target_id: uuid.UUID,
    file: UploadFile = File(...),
    ctx: ProjectContext = Depends(TRIGGER),
    db: Session = Depends(get_db),
):
    import io
    import tempfile
    from pathlib import Path

    from PIL import Image, ImageOps

    from app.core import audit
    from app.core.errors import CapabilityUnsupported
    from app.models.generation import Generation
    from app.storage import cas

    jobs.validate_target(db, ctx.project.id, target_type, target_id)
    data = file.file.read(100 * 1024 * 1024 + 1)
    if not data or len(data) > 100 * 1024 * 1024:
        raise CapabilityUnsupported("请上传不超过 100 MB 的图片或 MP4")
    typ = "image"
    try:
        if file.content_type == "video/mp4":
            typ = "video"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "input.mp4"
                path.write_bytes(data)
                jobs.media_engine.run(
                    [
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(path),
                        "-map",
                        "0:v:0",
                        "-t",
                        "0.1",
                        "-f",
                        "null",
                        "-",
                    ],
                    timeout=30,
                )
        else:
            with Image.open(io.BytesIO(data)) as im:
                if im.width * im.height > 40_000_000:
                    raise ValueError("图片超过 4000 万像素")
                buf = io.BytesIO()
                ImageOps.exif_transpose(im).save(buf, "PNG")
                data = buf.getvalue()
    except Exception as exc:
        raise CapabilityUnsupported("文件无法解码，请选择有效图片或 MP4") from exc
    blob = cas.put_bytes(db, data, "video/mp4" if typ == "video" else "image/png")
    job = GenerationJob(
        project_id=ctx.project.id,
        target_type=target_type,
        target_id=target_id,
        provider="local",
        request_type=typ,
        status="succeeded",
        estimated_cost=0,
        actual_cost=0,
        created_by=ctx.user.id,
        input_snapshot={"operation": "upload", "filename": file.filename},
    )
    jobs.event(job, "succeeded", "上传完成")
    db.add(job)
    db.flush()
    gen = Generation(
        project_id=ctx.project.id,
        job_id=job.id,
        target_type=target_type,
        target_id=target_id,
        provider="local",
        output_type=typ,
        output_blob_hash=blob.hash,
        cost_points=0,
        input_refs={"operation": "upload", "filename": file.filename},
    )
    db.add(gen)
    audit.record(
        db,
        action="media.upload",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type=target_type,
        target_id=target_id,
        detail={"job": str(job.id)},
    )
    db.flush()
    return gen


# ── 供应商 / 配额 ──
@router.put("/providers", response_model=schemas.ProviderConfigOut)
def configure_provider(
    data: schemas.ProviderConfigIn,
    ctx: ProjectContext = Depends(PROVIDER_MANAGE),
    db: Session = Depends(get_db),
):
    return schemas.ProviderConfigOut.model_validate(service.configure_provider(db, ctx, data))


@router.get("/providers", response_model=list[schemas.ProviderConfigOut])
def list_providers(
    ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)
):
    return [
        schemas.ProviderConfigOut.model_validate(p)
        for p in service.list_providers(db, ctx.project.id)
    ]


@router.post("/providers/test")
def test_provider(
    data: schemas.ProviderConfigIn,
    ctx: ProjectContext = Depends(PROVIDER_MANAGE),
    db: Session = Depends(get_db),
):
    ok, message = service.test_provider(
        db, ctx.project.id, data.provider_name, data.kind, data.endpoint, data.token, data.config
    )
    return {"ok": ok, "message": message}


@router.put("/quota", response_model=schemas.QuotaOut)
def set_quota(
    data: schemas.QuotaIn,
    ctx: ProjectContext = Depends(QUOTA_MANAGE),
    db: Session = Depends(get_db),
):
    return schemas.QuotaOut.model_validate(service.set_quota(db, ctx, data))


@router.get("/quota", response_model=schemas.QuotaOut | None)
def get_quota(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    q = service.get_project_quota(db, ctx.project.id)
    return schemas.QuotaOut.model_validate(q) if q else None


# ── 生成 ──
@router.post("/shots/{shot_id}/estimate", response_model=schemas.EstimateOut)
def estimate(
    shot_id: uuid.UUID,
    data: schemas.GenerateIn,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
    return service.estimate(db, ctx, "shot", shot_id, data)


@router.post("/shots/{shot_id}/generate", response_model=schemas.JobOut)
def generate(
    shot_id: uuid.UUID,
    data: schemas.GenerateIn,
    ctx: ProjectContext = Depends(TRIGGER),
    db: Session = Depends(get_db),
):
    return schemas.JobOut.model_validate(service.submit(db, ctx, "shot", shot_id, data))


@router.post("/assets/{asset_id}/estimate", response_model=schemas.EstimateOut)
def estimate_asset(
    asset_id: uuid.UUID,
    data: schemas.GenerateIn,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
    return service.estimate(db, ctx, "asset", asset_id, data)


@router.post("/assets/{asset_id}/generate", response_model=schemas.JobOut)
def generate_asset(
    asset_id: uuid.UUID,
    data: schemas.GenerateIn,
    ctx: ProjectContext = Depends(TRIGGER),
    db: Session = Depends(get_db),
):
    return schemas.JobOut.model_validate(service.submit(db, ctx, "asset", asset_id, data))


@router.get("/jobs/{job_id}", response_model=schemas.JobOut)
def get_job(
    job_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
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
    gen_id: uuid.UUID,
    data: schemas.RatePatch,
    ctx: ProjectContext = Depends(TRIGGER),
    db: Session = Depends(get_db),
):
    return schemas.GenerationOut.model_validate(
        service.patch_generation(db, ctx.project.id, gen_id, data)
    )


@router.post("/generations/{gen_id}/select", response_model=schemas.GenerationOut)
def select_variant(
    gen_id: uuid.UUID, ctx: ProjectContext = Depends(TRIGGER), db: Session = Depends(get_db)
):
    return schemas.GenerationOut.model_validate(service.select_variant(db, ctx, gen_id))
