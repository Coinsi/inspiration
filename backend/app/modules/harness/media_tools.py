"""Production tools backed by existing business jobs, quotas and immutable media.

Only trusted application code populates ToolContext.input_context.execution.
Model arguments cannot supply a request key, budget, or authorization. Generated
media remains an unselected candidate; success requires materialized CAS output.
"""

import copy
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, Forbidden, QuotaExceeded
from app.core.permissions import require
from app.models.creation import Creation
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.models.storage import Blob
from app.modules.generation import jobs, workbench
from app.modules.harness.context import ContextReference, revalidate_context
from app.modules.harness.media_context import describe_media, inspect_media
from app.modules.skill.schemas import SkillUse
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy
from app.storage import cas


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class GenerateArgs(Strict):
    provider: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=6000)
    request_type: Literal["image", "video"] = "image"
    count: int = Field(default=1, ge=1, le=8)
    params: dict = Field(default_factory=dict)
    provider_params: dict = Field(default_factory=dict)
    references: list[workbench.Reference] = Field(default_factory=list, max_length=8)
    skills: list[SkillUse] = Field(default_factory=list, max_length=4)
    work_id: uuid.UUID | None = None


class JobArgs(Strict):
    job_id: uuid.UUID


class InspectArgs(Strict):
    reference: ContextReference
    metadata_only: bool = False
    frame_times_ms: list[Annotated[int, Field(ge=0, strict=True)]] = Field(
        default_factory=list, max_length=4
    )
    include_transcript: bool = True
    include_audio: bool = False


class PreviewStatusArgs(Strict):
    work_id: uuid.UUID
    change_id: uuid.UUID
    output_id: str | None = Field(default=None, max_length=200)


def _points(value):
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation) as exc:
        raise CapabilityUnsupported("费用必须是有效非负数值") from exc
    if not result.is_finite() or result < 0:
        raise CapabilityUnsupported("费用必须是有效非负数值")
    return result


def _create_request(context, args, request_key):
    work_id = args.work_id or context.work_id
    if args.work_id and str(args.work_id) != str(context.work_id):
        raise Forbidden("生成目标不属于当前关联的作品")
    selected = {(str(s["id"]), s["revision"]) for s in context.skill_snapshot}
    if any((str(s.id), s.revision) not in selected for s in args.skills):
        raise Forbidden("生成只能使用本任务已选择的固定技能版本")
    raw = args.model_dump(exclude={"prompt"})
    raw.update(prompt_override=args.prompt, request_key=request_key, work_id=work_id)
    return workbench.Create.model_validate(raw)


def estimate(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    require(ctx.role, "generation.trigger")
    data = _create_request(context, args, uuid.UUID(int=0))
    cost = workbench.estimate(data, ctx, db)
    return {
        "estimated_points": float(_points(cost.points)),
        "detail": cost.detail,
        "request_type": args.request_type,
        "reference_count": len(args.references),
        "is_mock": args.provider == "mock",
        "requires_execution_authorization": True,
        "submitted": False,
    }


def _execution(db, ctx, context):
    grant = context.input_context.get("execution", {})
    if grant.get("generation_authorized") is not True:
        raise Forbidden("生成尚未取得执行授权")
    try:
        run_id, task_id, request_key = (
            uuid.UUID(str(grant[k])) for k in ("run_id", "task_id", "request_key")
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise Forbidden("生成需要系统持久保存的任务标识和请求标识") from exc
    run = db.scalar(
        select(ProductionRun)
        .where(ProductionRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    task = db.get(ProductionTask, task_id, populate_existing=True)
    if (
        not run
        or run.project_id != ctx.project.id
        or run.created_by != ctx.user.id
        or not task
        or task.run_id != run.id
    ):
        raise Forbidden("执行授权不属于当前用户和项目任务")
    if run.status not in {"queued", "running"} or task.status != "running":
        raise Conflict("制作步骤已暂停、结束或正在等待确认，不能开始新的生成")
    if run.context.get("guidance_pending"):
        raise Conflict("正在评估用户的新指导，暂不提交新的生成")
    if (
        str(task.lease_token) != str(grant.get("lease_token"))
        or not task.lease_expires_at
        or task.lease_expires_at <= datetime.now(UTC)
    ):
        raise Conflict("制作步骤的执行租约已过期，不能提交迟到的生成")
    if (
        "generate" not in run.policy.get("effects", [])
        or _points(run.policy.get("budget_points", 0)) <= 0
    ):
        raise Forbidden("制作任务没有生成授权和可用预算")
    if str(run.work_id or "") != str(context.work_id or ""):
        raise Forbidden("任务的关联作品已变化")
    return run, task, request_key, grant


def reserve_run_budget(db, run, request_key, estimated_points, job, *, limit=None):
    """Caller holds the freshly loaded run row lock and hasn't dispatched the job.

    Failed/canceled jobs without confirmed actual cost keep their reservation;
    an uncertain provider response never releases money for another submission.
    """
    requested = _points(estimated_points)
    limit_points = _points(run.policy.get("budget_points", 0))
    if limit is not None and requested > _points(limit):
        raise QuotaExceeded("本次生成估价超过授权上限")
    ledger = copy.deepcopy(run.context.get("budget_reservations", {}))
    key = str(request_key)
    prior = ledger.get(key)
    if prior:
        if prior.get("job_id") != str(job.id):
            raise Conflict("同一请求已预留另一个生成任务")
        return
    held = Decimal(0)
    for entry in ledger.values():
        old_job = db.get(GenerationJob, uuid.UUID(entry["job_id"]), populate_existing=True)
        if old_job and old_job.project_id == run.project_id and old_job.actual_cost is not None:
            held += _points(old_job.actual_cost)
        else:
            held += _points(entry["estimated_points"])
    if held + requested > limit_points:
        raise QuotaExceeded("制作预算不足，已包含其他并行步骤的预留费用")
    ledger[key] = {"job_id": str(job.id), "estimated_points": str(requested)}
    run.context = {**run.context, "budget_reservations": ledger}
    db.flush()


def _prior_job(db, ctx, data):
    prior = db.scalar(
        select(Creation).where(
            Creation.project_id == ctx.project.id,
            Creation.created_by == ctx.user.id,
            Creation.request_key == data.request_key,
        )
    )
    if not prior:
        return None
    payload = data.model_dump(mode="json", exclude={"request_key"})
    if payload.get("work_id") is None:
        payload.pop("work_id", None)
    if prior.request != payload:
        raise Conflict("系统请求标识已用于不同生成参数，不能覆盖或重复提交")
    job = db.scalar(
        select(GenerationJob)
        .where(
            GenerationJob.project_id == ctx.project.id,
            GenerationJob.target_type == "creation",
            GenerationJob.target_id == prior.id,
        )
        .order_by(GenerationJob.created_at, GenerationJob.id)
    )
    if not job:
        raise Conflict("已有创作记录缺少任务，请先核对状态，不能重新提交")
    return job


def generate(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    require(ctx.role, "generation.trigger")
    run, task, request_key, grant = _execution(db, ctx, context)
    data = _create_request(context, args, request_key)
    previous = _prior_job(db, ctx, data)
    if previous:
        origin = (previous.input_snapshot or {}).get("harness_origin", {})
        if origin.get("run_id") != str(run.id) or origin.get("task_id") != str(task.id):
            raise Conflict("系统请求标识属于另一个执行步骤")
        # The receipt may have been lost after dispatch. Observe the saved job only.
        return observe_job(db, ctx, previous)
    quoted = workbench.estimate(data, ctx, db)
    _points(quoted.points)
    # Creation and reservation roll back together if quotas/config changed.
    with db.begin_nested():
        job = workbench.create_job(db, ctx, data)
        reserve_run_budget(
            db,
            run,
            request_key,
            max(_points(quoted.points), _points(job.estimated_cost)),
            job,
            limit=grant.get("budget_limit_points"),
        )
        from app.modules.harness.resources import reserve_units

        reserve_units(
            run,
            "generate:" + str(request_key),
            {
                "output_storage_bytes": args.count * 256 * 1024 * 1024,
                "media_transfer_bytes": args.count * 256 * 1024 * 1024,
            },
            job_id=job.id,
        )
        job.input_snapshot = {
            **job.input_snapshot,
            "harness_resource_limits": {
                "output_bytes": args.count * 256 * 1024 * 1024,
                "reservation_key": "generate:" + str(request_key),
            },
            "harness_origin": {
                "run_id": str(run.id),
                "task_id": str(task.id),
                "request_key": str(request_key),
                "plan_revision": task.revision,
            },
        }
        db.flush()
    # Existing dispatcher commits job + budget before contacting the executor.
    jobs.dispatch(db, job)
    return observe_job(db, ctx, job)


def observe_job(db, ctx, job, *, check_storage=True):
    jobs.validate_target(db, ctx.project.id, job.target_type, job.target_id)
    outputs = []
    for generation in db.scalars(
        select(Generation)
        .where(
            Generation.project_id == ctx.project.id,
            Generation.job_id == job.id,
        )
        .order_by(Generation.created_at, Generation.id)
    ):
        blob = db.get(Blob, generation.output_blob_hash) if generation.output_blob_hash else None
        accessible = False
        if blob and blob.size_bytes > 0:
            if check_storage:
                try:
                    with cas.open_range(blob, 0, 1) as stream:
                        accessible = bool(stream.read(1))
                except (OSError, ValueError):
                    pass
        outputs.append(
            {
                "id": str(generation.id),
                "kind": generation.output_type,
                "blob_hash": generation.output_blob_hash,
                "storage_accessible": accessible if check_storage else None,
                "record_ready": bool(blob and blob.size_bytes > 0),
                "reference": {
                    "kind": "generation",
                    "id": str(generation.id),
                    "expected_blob_hash": generation.output_blob_hash,
                },
                "is_selected": generation.is_selected,
                "checks": (["blob_record", "readable_prefix"] if accessible else ["blob_record"])
                if blob and blob.size_bytes > 0
                else [],
            }
        )
    return {
        "job_id": str(job.id),
        "status": job.status,
        "estimated_points": float(job.estimated_cost or 0),
        "actual_points": float(job.actual_cost) if job.actual_cost is not None else None,
        "error": job.error,
        "outputs": outputs,
        "records_ready": job.status == "succeeded"
        and bool(outputs)
        and all(g["record_ready"] for g in outputs),
        "materialized": job.status == "succeeded"
        and bool(outputs)
        and all(g["storage_accessible"] for g in outputs),
        "is_mock": job.provider == "mock",
        "adopted": False,
        "unverified": ["full_decode", "creative_quality"],
    }


def job_observe(db, ctx, context, args):
    from app.modules.generation.service import get_job

    ctx = revalidate_context(db, ctx)
    return observe_job(db, ctx, get_job(db, ctx.project.id, args.job_id))


def inspect(db, ctx, context, args):
    if args.metadata_only:
        if args.frame_times_ms or args.include_audio:
            raise CapabilityUnsupported("仅元数据模式不能同时要求抽帧或试听")
        return describe_media(db, ctx, args.reference)
    return inspect_media(
        db,
        ctx,
        args.reference,
        frame_times_ms=args.frame_times_ms,
        include_transcript=args.include_transcript,
        include_audio=args.include_audio,
    )


def preview_status(db, ctx, context, args):
    from app.modules.engineering import review, service

    ctx = revalidate_context(db, ctx)
    service.work(db, ctx, args.work_id)
    change = review.get_change(db, args.work_id, args.change_id)
    if args.output_id is None and change.preview_job_id:
        job = db.scalar(
            review.preview_query(ctx, change).where(GenerationJob.id == change.preview_job_id)
        )
    else:
        job = review.find_preview(db, ctx, change, args.output_id)
    if not job:
        return {
            "change_id": str(change.id),
            "status": "not_started",
            "job_id": None,
            "materialized": False,
            "outputs": [],
            "adopted": False,
        }
    return {"change_id": str(change.id), **observe_job(db, ctx, job)}


def register_tools():
    """Called by application composition, never by a model-supplied plugin path."""
    definitions = [
        (
            "media.estimate",
            "校验图片或视频生成输入与参考，返回估价；不会提交生成。",
            GenerateArgs,
            estimate,
            ToolPolicy(
                effect="read",
                permissions=("generation.trigger",),
                idempotency="read_only",
                cancellation="not_applicable",
                recovery="repeat_read",
                output_kind="media_estimate",
            ),
        ),
        (
            "media.generate",
            "在系统明确授权和预留预算内生成图片或视频候选；支持模型实际声明的参考输入。"
            "请求标识由系统注入，不能自行传入。返回 job_id 后须观察任务及入库结果，不自动采用。",
            GenerateArgs,
            generate,
            ToolPolicy(
                effect="generate",
                permissions=("generation.trigger",),
                idempotency="business_key",
                cancellation="job_cooperative",
                recovery="query_job",
                charges_on_invoke=True,
                starts_job=True,
                output_kind="media_generation_job",
                output_schema={"type": "object", "required": ["job_id", "status", "materialized"]},
            ),
        ),
        (
            "media.job_observe",
            "查询实际任务与持久素材可用性，区分上游成功与成果可读取。",
            JobArgs,
            job_observe,
            ToolPolicy(
                effect="read",
                idempotency="read_only",
                cancellation="not_applicable",
                recovery="repeat_read",
                output_kind="materialized_job",
            ),
        ),
        (
            "media.inspect",
            "检查固定图片、最多4个视频帧或最多15秒声音，明确检查范围与未覆盖内容；"
            "抽样不能证明整段视频质量。",
            InspectArgs,
            inspect,
            ToolPolicy(
                effect="read",
                idempotency="read_only",
                cancellation="not_applicable",
                recovery="repeat_read",
                output_kind="bounded_media_observation",
            ),
        ),
        (
            "engineering.preview_status",
            "查询指定候选的实际预览任务及持久结果，不启动预览或采用。",
            PreviewStatusArgs,
            preview_status,
            ToolPolicy(
                effect="read",
                idempotency="read_only",
                cancellation="not_applicable",
                recovery="repeat_read",
                output_kind="preview_status",
            ),
        ),
    ]
    for name, description, schema, handler, policy in definitions:
        if name not in registry.CATALOG:
            registry.register(
                registry.Tool(
                    name,
                    description,
                    schema,
                    group="engineering" if name.startswith("engineering.") else "media",
                ),
                handler,
            )
            capabilities.register_contract(name, policy)
