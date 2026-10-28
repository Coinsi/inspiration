"""Receive an accepted remote job again without creating or submitting a new one.

The provider wrappers guard this marker before selecting their resume branch.
Removing its saved handle can therefore never fall through to adapter.submit.
"""

import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

from app.core.errors import Conflict, Forbidden, NotFound
from app.core.permissions import require
from app.models.generation import Generation, GenerationJob, ModelChannel, ProviderConfig
from app.models.harness import ProductionRun, ProductionTask
from app.models.identity import Project
from app.modules.generation import jobs, model_catalog, personal
from app.modules.generation.model_routing import resumable_profile
from app.modules.harness.context import revalidate_context
from app.modules.harness.media_tools import JobArgs, _execution, observe_job
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy

MARKER = "harness_receipt_recovery"


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _route(job):
    state, snapshot = job.routing_state or {}, job.input_snapshot or {}
    if job.provider.startswith(personal.PREFIX):
        return {"provider_name": job.provider, "model_profile": snapshot.get("model_profile", {})}
    if not job.provider.startswith(model_catalog.PREFIX):
        return None  # Bare adapters have no durable submit-bypass contract.
    return next(
        (
            r
            for r in snapshot.get("routing", {}).get("candidates", [])
            if r.get("provider_name") == state.get("provider_name")
        ),
        None,
    )


def resume_problem(job):
    """Cheap eligibility for UI; execution additionally checks ownership and live config."""
    if job.status != "failed":
        return "仅失败任务可以重新接收；进行中或已保存的任务只需查看状态"
    if job.actual_cost is not None:
        return "任务已有扣费与完成记录，请查看原结果，不能再次接收"
    state = job.routing_state or {}
    if state.get("phase") not in {"accepted", "generated"}:
        return "尚未确认上游已接受且可继续查询；不会重发未知提交或上游失败的任务"
    route = _route(job)
    if not route or not resumable_profile(route.get("model_profile", {})):
        return "原服务协议不支持安全恢复查询"
    handle = state.get("external_job_id")
    if not isinstance(handle, str) or not handle or handle != job.external_job_id:
        return "原任务句柄缺失或不一致，无法安全恢复"
    resume = state.get("resume")
    if resume and (
        resume.get("external_job_id") != handle
        or resume.get("provider_name") != route["provider_name"]
    ):
        return "已保存的恢复句柄与原任务不一致"
    if (
        job.provider.startswith(personal.PREFIX)
        and state.get("phase") == "accepted"
        and not state.get("receipt_poll_tracking")
    ):
        return "旧个人服务记录无法区分查询中断与上游失败，请先核对原任务"
    origin = (job.input_snapshot or {}).get("harness_origin", {})
    if not all(origin.get(k) for k in ("run_id", "task_id", "request_key")):
        return "缺少原制作任务与预算归属记录"
    return None


def _config_digest(db, job):
    """Original revisions establish identity; encrypted credential bytes are only hashed."""
    snapshot, route = job.input_snapshot or {}, _route(job)
    if not route:
        raise Conflict("原模型线路已无法确定")
    if job.provider.startswith(personal.PREFIX):
        row, key = personal.resolve(db, job.provider, job.created_by, snapshot)
        config = personal.profile(row.models[key])
        if config != snapshot.get("model_profile"):
            raise Conflict("个人模型参数已改变，不能恢复原任务")
        identity = {
            "id": str(row.id),
            "revision": row.revision,
            "key": key,
            "endpoint": row.endpoint,
            "config": config,
            "credential": hashlib.sha256(row.credentials_encrypted or b"").hexdigest(),
        }
    else:
        # Configuration writers serialize on Project before locking a channel,
        # profile and model. Follow that order to avoid a recovery/edit deadlock.
        db.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
        from app.modules.generation.model_scope import lock_model_scope

        scope = model_catalog.get_model(
            db, job.project_id, job.provider, allow_global=True
        ).project_id
        lock_model_scope(db, scope)
        model = model_catalog.get_model(
            db, job.project_id, job.provider, lock=True, allow_global=True
        )
        profile, channel, reason = model_catalog.route_profile(db, model.project_id, route)
        if reason or not model.enabled:
            raise Conflict("原模型或服务已停用，不能恢复原任务")
        # Keep the checked connection stable until the provider wrapper has built
        # its adapter and records the accepted handle in the same transaction.
        if profile and channel:
            profile = db.scalar(
                select(ProviderConfig)
                .where(ProviderConfig.id == profile.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            channel = db.scalar(
                select(ModelChannel)
                .where(ModelChannel.id == channel.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if (
            model.revision != snapshot.get("routing", {}).get("revision")
            or not profile
            or not channel
            or not profile.enabled
            or not channel.enabled
            or str(channel.id) != route.get("channel_id")
            or channel.revision != route.get("channel_revision")
            or hashlib.sha256(channel.endpoint.encode()).hexdigest() != route.get("endpoint_hash")
            or profile.config != route.get("model_profile")
        ):
            raise Conflict("原模型或服务配置已改变，不能向变更后的线路恢复任务")
        identity = {
            "model_id": str(model.id),
            "revision": model.revision,
            "channel_id": str(channel.id),
            "channel_revision": channel.revision,
            "endpoint": channel.endpoint,
            "config": profile.config,
            "credential": hashlib.sha256(channel.credentials_encrypted or b"").hexdigest(),
        }
    return _digest(identity)


def _related(db, run, task, source_id):
    """Explicit dependencies/replacements only; incidental same-run work is not authority."""
    rows = {
        str(t.id): t
        for t in db.scalars(select(ProductionTask).where(ProductionTask.run_id == run.id))
    }
    pending, visited = [str(task.id)], set()
    while pending:
        ident = pending.pop()
        if ident in visited or ident not in rows:
            continue
        visited.add(ident)
        if ident == str(source_id):
            return True
        row = rows[ident]
        pending.extend(str(v) for v in row.dependencies)
        pending.extend(str(v) for v in row.state.get("replaces", []))
    return False


def _owned(db, ctx, run, task, job):
    origin = (job.input_snapshot or {}).get("harness_origin", {})
    if (
        job.project_id != ctx.project.id
        or job.created_by != ctx.user.id
        or origin.get("run_id") != str(run.id)
        or not _related(db, run, task, origin.get("task_id"))
    ):
        raise Forbidden("不能恢复其他用户、制作任务或无明确依赖的生成记录")
    reservation = run.context.get("budget_reservations", {}).get(origin.get("request_key"), {})
    if reservation.get("job_id") != str(job.id):
        raise Conflict("原任务没有可核对的生成预算预留，不会新建或追加生成")
    jobs.validate_target(db, ctx.project.id, job.target_type, job.target_id)


def _no_outputs(db, job):
    if job.actual_cost is not None or db.scalar(
        select(Generation.id).where(Generation.job_id == job.id).limit(1)
    ):
        raise Conflict("原任务已有成果或扣费记录，请查看已有结果，不能重新接收")


def validate_resume_guard(db, job):
    """Worker-side guard, called before either wrapper could invoke submit."""
    saved = (job.routing_state or {}).get(MARKER)
    if not saved:
        return
    frozen, resume = saved.get("frozen", {}), (job.routing_state or {}).get("resume", {})
    if (
        saved.get("version") != 1
        or frozen.get("snapshot_hash") != _digest(job.input_snapshot)
        or frozen.get("provider") != job.provider
        or frozen.get("external_job_id") != job.external_job_id
        or resume.get("external_job_id") != job.external_job_id
        or resume.get("provider_name") != (_route(job) or {}).get("provider_name")
    ):
        raise Conflict("恢复快照或句柄已改变；已阻止重新提交生成")
    try:
        run = db.get(ProductionRun, uuid.UUID(frozen["run_id"]), populate_existing=True)
        task = db.get(ProductionTask, uuid.UUID(frozen["task_id"]), populate_existing=True)
    except (ValueError, KeyError):
        raise Conflict("恢复任务归属无效") from None
    if (
        not run
        or not task
        or task.run_id != run.id
        or run.status in {"canceled", "succeeded"}
        or task.status in {"canceled", "superseded", "succeeded"}
    ):
        raise Conflict("原制作步骤已结束或被替代，不能继续接收")
    ctx = revalidate_context(
        db,
        SimpleNamespace(
            project=SimpleNamespace(id=job.project_id), user=SimpleNamespace(id=job.created_by)
        ),
    )
    require(ctx.role, "generation.trigger")
    _owned(db, ctx, run, task, job)
    _no_outputs(db, job)
    if _config_digest(db, job) != frozen.get("config_digest"):
        raise Conflict("排队期间原服务配置发生变化，已停止接收且不会重新生成")


def resume_receipt(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    require(ctx.role, "generation.trigger")
    run, task, request_key, _ = _execution(db, ctx, context)
    job = db.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == args.job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not job:
        raise NotFound("原生成任务不存在")
    _owned(db, ctx, run, task, job)
    state = copy.deepcopy(job.routing_state or {})
    saved = state.get(MARKER, {})
    if str(request_key) in saved.get("request_keys", []):
        return {**observe_job(db, ctx, job), "receipt_only": True, "reused_request": True}
    if problem := resume_problem(job):
        raise Conflict(problem)
    _no_outputs(db, job)
    keys = saved.get("request_keys", [])
    if len(keys) >= min(5, run.policy.get("max_retries", 2)):
        raise Conflict("重新接收已达到上限，请核对原服务和存储状态")
    digest = _config_digest(db, job)
    if saved and (
        saved["frozen"]["config_digest"] != digest
        or saved["frozen"]["snapshot_hash"] != _digest(job.input_snapshot)
    ):
        raise Conflict("恢复快照已改变，不能继续接收")
    state[MARKER] = {
        "version": 1,
        "request_keys": [*keys, str(request_key)],
        "last_requested_at": datetime.now(UTC).isoformat(),
        "frozen": {
            "run_id": str(run.id),
            "task_id": str(task.id),
            "provider": job.provider,
            "external_job_id": job.external_job_id,
            "snapshot_hash": _digest(job.input_snapshot),
            "config_digest": digest,
        },
    }
    state["resume"] = {
        "source_job_id": str(job.id),
        "provider_name": _route(job)["provider_name"],
        "external_job_id": job.external_job_id,
    }
    job.routing_state, job.status, job.error = state, "pending", None
    jobs.dispatch(db, job)
    return {**observe_job(db, ctx, job), "receipt_only": True, "reused_request": False}


def register_tools():
    if "media.resume_receipt" in registry.CATALOG:
        return
    registry.register(
        registry.Tool(
            "media.resume_receipt",
            "继续查询已接受的视频生成任务并接收文件，不提交新生成，不重复预留费用；未知提交与上游失败不可重发。",
            JobArgs,
            group="media",
        ),
        resume_receipt,
    )
    capabilities.register_contract(
        "media.resume_receipt",
        ToolPolicy(
            effect="generate",
            permissions=("generation.trigger",),
            idempotency="business_key",
            cancellation="job_cooperative",
            recovery="query_job",
            charges_on_invoke=False,
            starts_job=True,
            output_kind="media_generation_job",
        ),
    )
