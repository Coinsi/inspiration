"""Direct creation from the workbench, using normal jobs, quotas and provenance."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.contracts import ReferenceImage, reference_error
from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.core.errors import CapabilityUnsupported, Conflict
from app.models.creation import Creation
from app.models.generation import GenerationJob
from app.models.identity import Project
from app.modules.generation import jobs, schemas, service
from app.modules.generation import references as reference_media
from app.modules.generation.references import Reference

router = APIRouter(prefix="/projects/{project_id}/workbench", tags=["workbench"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
RUN = Depends(require_action("generation.trigger"))


class Create(schemas.GenerateIn):
    work_id: uuid.UUID | None = None
    model_config = ConfigDict(extra="forbid")
    request_key: uuid.UUID
    prompt_override: str = Field(min_length=1, max_length=6000)
    provider: str = Field(min_length=1, max_length=120)
    references: list[Reference] = Field(default_factory=list, max_length=8)

    @field_validator("prompt_override")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请填写创作想法")
        return value.strip()


def prepare(db, ctx, data):
    if data.work_id:
        from app.modules.conversation.works import get as get_work

        get_work(db, ctx.project.id, data.work_id)
    if data.request_type.value not in ("image", "video"):
        raise CapabilityUnsupported("工作台媒体创作支持图片与视频")
    provider = service._provider_instance(db, ctx.project.id, data.provider, user_id=ctx.user.id)
    caps = provider.capabilities()
    service._validate_controls(data, caps)
    if data.request_type.value not in caps.modalities:
        raise CapabilityUnsupported("模型不支持当前创作类型")
    refs = []
    unique = set()
    for ref in data.references:
        key = (ref.source_type, ref.source_id)
        if key in unique:
            raise CapabilityUnsupported("同一素材不能重复引用")
        unique.add(key)
        item = reference_media.resolve(db, ctx.project.id, ref)
        if item["kind"] == "video" and data.request_type.value != "video":
            raise CapabilityUnsupported("参考视频只能用于生成视频")
        if ref.role != "reference" and (
            data.request_type.value != "video" or ref.role not in caps.features
        ):
            raise CapabilityUnsupported("所选模型不支持此首尾帧输入")
        if ref.role != "reference" and sum(r.role == ref.role for r in data.references) > 1:
            raise CapabilityUnsupported("首帧和尾帧各只能选一张")
        refs.append(ReferenceImage(blob_hash=item["blob_hash"], role=ref.role, kind=item["kind"]))
    if problem := reference_error(refs, caps):
        raise CapabilityUnsupported(problem)
    gen = schemas.GenerateIn.model_validate(
        data.model_dump(exclude={"request_key", "references", "work_id"})
    )
    gen.use_references = False
    # The composer exposes one reference path. Avoid combining hidden legacy fields.
    if any((gen.source_generation_id, gen.first_frame_id, gen.last_frame_id)):
        raise CapabilityUnsupported("请通过参考内容选择图片")
    return provider, gen, refs


@router.post("/estimate")
def estimate(data: Create, ctx: ProjectContext = RUN, db: Session = DB):
    from app.modules.identity.preferences import guide
    from app.modules.skill.service import compose

    provider, gen, refs = prepare(db, ctx, data)
    prompt = guide(db, ctx.project.id, gen.prompt_override, "generation")
    prompt, _ = compose(db, ctx.project.id, gen.skills, prompt)
    request = service._build_request(prompt, gen, refs)
    provider.validate_request(request)
    return provider.estimate_cost(request)


@router.post("/generate", response_model=schemas.JobOut)
def generate(data: Create, ctx: ProjectContext = RUN, db: Session = DB):
    job = create_job(db, ctx, data)
    if job.status != "pending" or (job.cost_raw or {}).get("events"):
        return job
    return jobs.dispatch(db, job)


def create_job(db, ctx, data):
    """Create without dispatch so callers can atomically attach business records."""
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    payload = data.model_dump(mode="json", exclude={"request_key"})
    if payload.get("work_id") is None:
        payload.pop("work_id", None)
    prior = db.scalar(
        select(Creation).where(
            Creation.project_id == ctx.project.id,
            Creation.created_by == ctx.user.id,
            Creation.request_key == data.request_key,
        )
    )
    if prior:
        if prior.request != payload:
            raise Conflict("此提交标识已用于另一份创作，请重新发送")
        return db.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == ctx.project.id,
                GenerationJob.target_type == "creation",
                GenerationJob.target_id == prior.id,
            )
            .order_by(GenerationJob.created_at, GenerationJob.id)
        )
    _, gen, refs = prepare(db, ctx, data)
    row = Creation(
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        request_key=data.request_key,
        title=data.prompt_override[:120],
        work_id=data.work_id,
        request=payload,
    )
    db.add(row)
    db.flush()
    job = service.submit(
        db, ctx, "creation", row.id, gen, extra_references=refs, defer_dispatch=True
    )
    job.input_snapshot = {
        **job.input_snapshot,
        "work_id": str(row.work_id) if row.work_id else None,
        "reference_sources": [r.model_dump(mode="json") for r in data.references],
    }
    return job


@router.get("/creations")
def history(
    ctx: ProjectContext = READ,
    db: Session = DB,
    offset: int = Query(0, ge=0),
    work_id: uuid.UUID | None = None,
):
    if work_id:
        from app.modules.conversation.works import get as get_work

        get_work(db, ctx.project.id, work_id)
    rows = list(
        db.scalars(
            select(Creation)
            .where(Creation.project_id == ctx.project.id)
            .where(Creation.work_id == work_id if work_id else True)
            .order_by(Creation.created_at.desc(), Creation.id)
            .offset(offset)
            .limit(12)
        )
    )
    return [
        {
            "id": row.id,
            "title": row.title,
            "work_id": row.work_id,
            "request": row.request,
            "jobs": [
                schemas.JobOut.model_validate(j)
                for j in db.scalars(
                    select(GenerationJob)
                    .where(
                        GenerationJob.project_id == ctx.project.id,
                        GenerationJob.target_type == "creation",
                        GenerationJob.target_id == row.id,
                    )
                    .order_by(GenerationJob.created_at.desc())
                )
            ],
            "outputs": [
                schemas.GenerationOut.model_validate(g)
                for g in service.list_generations(db, ctx.project.id, "creation", row.id)
            ],
        }
        for row in rows
    ]


@router.get("/references/{reference_id}")
def reference_detail(
    reference_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB,
    source_type: Literal["generation", "media_version"] = "generation",
):
    field = "media_version_id" if source_type == "media_version" else "generation_id"
    return reference_media.resolve(db, ctx.project.id, Reference(**{field: reference_id}))


@router.get("/references")
def references(
    ctx: ProjectContext = READ,
    db: Session = DB,
    offset: int = Query(0, ge=0),
    query: str = Query("", max_length=100),
    media_type: Literal["image", "video", "all"] = "image",
):
    return reference_media.catalog(
        db, ctx.project.id, query=query, media_type=media_type, offset=offset
    )
