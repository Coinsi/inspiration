"""Translate durable production runs into the existing conversation contract.

The conversation owns its history. A run can have several guidance turns, but
only its newest assistant message is a live progress projection. Earlier turns
stay as snapshots rather than being rewritten when a production is replanned.
"""

import copy
import math
import uuid

from sqlalchemy import select

from app.core.errors import AppError, Conflict, NotFound
from app.models.conversation import ConversationMessage
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionAttempt, ProductionEvent, ProductionRun
from app.modules.harness import schemas, service
from app.modules.tools.contracts import ToolContext

KIND = "production"
STATES = {
    "queued": "queued",
    "running": "running",
    "needs_plan_approval": "needs_review",
    "needs_attention": "needs_review",
    "paused": "paused",
    "succeeded": "completed",
    "failed": "failed",
    "canceled": "cancelled",
}


def _owned_run(db, ctx, conversation_id, run_id, *, lock=False):
    run = service.get_run(db, ctx, run_id, lock)
    if run.conversation_id != conversation_id or run.created_by != ctx.user.id:
        raise NotFound("制作任务不属于这段对话")
    return run


def _history_references(db, ctx, history):
    """Promote application-owned output IDs to references, never visual evidence."""
    from app.modules.harness.context import resolve_reference

    references, omitted, seen = [], [], set()
    for message in reversed(history.get("messages", [])):
        for output in reversed(message.get("media_outputs", [])):
            raw_id = output.get("id")
            if not raw_id or raw_id in seen:
                continue
            seen.add(raw_id)
            if len(references) >= 12:
                omitted.append({"id": raw_id, "reason": "history_reference_window"})
                continue
            try:
                ref = {
                    "kind": "generation",
                    "id": str(uuid.UUID(str(raw_id))),
                    "purpose": "先前对话中的成果；需要查看内容时先检查素材",
                }
                if output.get("blob_hash"):
                    ref["expected_blob_hash"] = output["blob_hash"]
                references.append(resolve_reference(db, ctx, ref)["reference"])
            except (AppError, ValueError, TypeError):
                # Optional historic media may have been removed or access revoked.
                # Explicit references are resolved separately and fail closed.
                omitted.append({"id": str(raw_id), "reason": "historical_media_unavailable"})
    return references, omitted


def _direct_plan(db, ctx, conversation, data):
    from app.modules.harness.media_tools import GenerateArgs, estimate
    from app.modules.skill.service import selected_versions

    args = GenerateArgs(
        **data.media.model_dump(),
        prompt=data.content,
        work_id=conversation.work_id,
        skills=data.skills,
        count=1,
    )
    quote = estimate(
        db,
        ctx,
        ToolContext(
            work_id=conversation.work_id,
            skill_snapshot=selected_versions(db, ctx.project.id, data.skills),
        ),
        args,
    )
    budget = max(1, math.ceil(quote["estimated_points"]))
    if budget > 1000000:
        raise Conflict("本次生成估价超出单次制作预算上限，请调整生成参数")
    label = "图片" if data.media.request_type == "image" else "视频"
    plan = schemas.Plan(
        summary=f"按本次要求生成{label}候选，保存后在对话中预览。",
        tasks=[
            schemas.TaskSpec(
                key="generate",
                goal=data.content,
                kind="tool",
                tool="media.generate",
                arguments=args.model_dump(mode="json"),
                expected_output="artifact",
            )
        ],
    )
    policy = schemas.Policy(effects=["read", "generate"], budget_points=budget, max_parallel=1)
    return plan, policy, quote


def _archive_projection(db, run):
    """Finish the old guidance bubble before attaching a new live projection."""
    publish(db, run)
    for message in db.scalars(
        select(ConversationMessage).where(
            ConversationMessage.execution_kind == KIND,
            ConversationMessage.execution_id == run.id,
            ConversationMessage.role == "assistant",
        )
    ):
        if message.context.get("production", {}).get("is_current") is False:
            continue
        if message.status in {"queued", "running", "needs_review", "paused"}:
            message.status = "completed"
            if not message.content:
                message.content = "已收到新的指导，制作进度将在后续消息中继续。"
        message.context = {
            **message.context,
            "production": {**message.context.get("production", {}), "is_current": False},
        }


def start(db, ctx, conversation, message, data, history):
    """Attach a new execution or a control turn; never dispatch external I/O here."""
    if (
        conversation.project_id != ctx.project.id
        or conversation.created_by != ctx.user.id
        or conversation.archived_at
    ):
        raise NotFound("对话不存在或已归档")
    db.flush()
    key = uuid.uuid5(conversation.id, str(data.request_key))
    request = data.model_dump(mode="json")
    guidance = getattr(data, "production", None)
    received_guidance = None
    omitted = []
    # Conversation sends hold the conversation lock. Resolve a pending production
    # before creating a new run, so ordinary feedback does not fork the work.
    continuation = None
    if not guidance and not data.media:
        prior_event = db.scalar(
            select(ProductionEvent)
            .join(ProductionRun)
            .where(
                ProductionRun.conversation_id == conversation.id,
                ProductionRun.created_by == ctx.user.id,
                ProductionEvent.request_key == key,
                ProductionEvent.kind == "user.guidance_received",
            )
        )
        if prior_event:
            continuation = db.get(ProductionRun, prior_event.run_id)
        elif not db.scalar(select(ProductionRun.id).where(ProductionRun.request_key == key)):
            active = list(
                db.scalars(
                    select(ProductionRun).where(
                        ProductionRun.conversation_id == conversation.id,
                        ProductionRun.created_by == ctx.user.id,
                        ProductionRun.status.in_(
                            [
                                "queued",
                                "running",
                                "paused",
                                "needs_plan_approval",
                                "needs_attention",
                            ]
                        ),
                    )
                )
            )
            if len(active) > 1:
                raise Conflict(
                    "这段对话有多个进行中的制作，请先选择要指导的任务",
                    {
                        "run_ids": [str(run.id) for run in active],
                    },
                )
            continuation = active[0] if active else None
    if continuation:
        from app.modules.harness import guidance as inbox

        run = continuation
        _archive_projection(db, run)
        received_guidance = inbox.receive(db, ctx, run.id, key, data.content)["guidance_id"]
        run.context = {
            **run.context,
            "history": copy.deepcopy(history.get("messages", [])),
            "history_truncated": bool(history.get("history_truncated")),
        }
    elif guidance:
        run = _owned_run(db, ctx, conversation.id, guidance.run_id, lock=True)
        _archive_projection(db, run)
        if guidance.action in {"guidance", "confirm_guidance"}:
            from app.modules.harness import guidance as inbox

            if guidance.action == "guidance":
                if run.revision != guidance.expected_revision:
                    raise Conflict("制作状态已变化，请刷新后继续指导")
                received_guidance = inbox.receive(
                    db,
                    ctx,
                    run.id,
                    key,
                    data.content,
                    {"task_ids": [str(v) for v in guidance.task_ids]},
                )["guidance_id"]
            else:
                if not guidance.guidance_id:
                    raise Conflict("请选择需要确认的指导意见")
                inbox.confirm(
                    db, ctx, run.id, guidance.guidance_id, key, guidance.expected_revision
                )
                received_guidance = str(guidance.guidance_id)
        else:
            service.control(
                db,
                ctx,
                run.id,
                schemas.Control(
                    request_key=key,
                    expected_revision=guidance.expected_revision,
                    action=guidance.action,
                    task_ids=guidance.task_ids,
                    message=data.content,
                    budget_points=getattr(guidance, "budget_points", None),
                ),
            )
        # The actual decision is preserved by harness.control; the conversation
        # history remains quoted supporting context, never an implicit override.
        run.context = {
            **run.context,
            "history": copy.deepcopy(history.get("messages", [])),
            "history_truncated": bool(history.get("history_truncated")),
        }
    else:
        prior = db.scalar(
            select(ProductionRun).where(
                ProductionRun.project_id == ctx.project.id,
                ProductionRun.created_by == ctx.user.id,
                ProductionRun.request_key == key,
            )
        )
        if prior:
            if (
                prior.conversation_id != conversation.id
                or prior.context.get("conversation_request") != request
            ):
                raise Conflict("同一消息标识不能用于不同创作要求")
            run = prior
        else:
            references, omitted = _history_references(db, ctx, history)
            plan, policy, quote = None, schemas.Policy(), None
            if data.media:
                plan, policy, quote = _direct_plan(db, ctx, conversation, data)
                # Direct generation uses only the references explicitly selected
                # for this request; prior history does not silently alter input.
                references = [
                    {
                        "kind": "generation",
                        "id": str(ref.generation_id),
                        "purpose": ref.role,
                        "required": True,
                    }
                    for ref in data.media.references
                ]
            value = service.create(
                db,
                ctx,
                schemas.Create(
                    goal=data.content,
                    request_key=key,
                    work_id=conversation.work_id,
                    conversation_id=conversation.id,
                    engine="mock" if data.mode == "test" else "cloud_llm",
                    model_provider=None if data.mode == "test" else data.model_provider,
                    scope=data.scope,
                    skills=[s.id for s in data.skills],
                    skill_versions=data.skills,
                    references=references,
                    policy=policy,
                    plan=plan,
                ),
            )
            run = db.get(ProductionRun, value["id"])
            run.context = {
                **run.context,
                "conversation_request": request,
                "history": copy.deepcopy(history.get("messages", [])),
                "history_truncated": bool(history.get("history_truncated")),
                "history_reference_omissions": omitted,
                "direct_request": bool(data.media),
            }
            if data.media:
                run.context = {**run.context, "estimate": quote}
                # control refreshes the locked row; persist the newly created
                # run's event sequence and exact request before that refresh.
                db.flush()
                # The explicit generate submission is the user's authorization
                # for this exact one-step plan, not for later autonomous charges.
                service.control(
                    db,
                    ctx,
                    run.id,
                    schemas.Control(
                        request_key=uuid.uuid5(key, "explicit-generation"),
                        expected_revision=run.revision,
                        action="approve_plan",
                        message="用户在对话中明确提交了本次生成请求。",
                    ),
                )
    message.execution_kind = KIND
    message.execution_id = run.id
    message.context = {
        "mode": data.mode,
        "model_provider": data.model_provider,
        "scope": [s.model_dump(mode="json") for s in data.scope],
        "skills": [s.model_dump(mode="json") for s in data.skills],
        "media": data.media.model_dump(mode="json") if data.media else None,
        # Keep the submitted command separate from live production metadata for
        # message idempotency and safe retry behavior.
        "production_request": guidance.model_dump(mode="json") if guidance else None,
        "guidance_id": received_guidance,
        "history_truncated": bool(history.get("history_truncated")),
        "history_reference_omissions": omitted,
        "production": {"run_id": str(run.id), "is_current": True},
    }
    _project(db, message, run)
    return service.out(db, run)


def _job_ids(rows):
    ids = set()
    for task in rows:
        receipts = [task.result, *task.result.get("receipts", [])]
        receipts.extend(s.get("receipt", {}) for s in task.state.get("steps", []))
        for receipt in receipts:
            data = receipt.get("data")
            if receipt.get("status") != "succeeded" or not isinstance(data, dict):
                continue
            # These tool receipts contain owned business jobs, not model suggestions.
            if receipt.get("tool") not in {
                "media.generate",
                "media.job_observe",
                "engineering.preview",
                "engineering.preview_status",
            }:
                continue
            value = data.get("job_id") or data.get("preview_job_id")
            if value:
                ids.add(uuid.UUID(str(value)))
    return ids


def _outputs(db, run, rows):
    from app.modules.harness.media_tools import observe_job

    result = []
    ctx = None
    for job_id in sorted(_job_ids(rows), key=str):
        job = db.get(GenerationJob, job_id)
        if not job or job.project_id != run.project_id or job.status != "succeeded":
            continue
        if ctx is None:
            ctx = service.execution_context(db, run)
        actual = observe_job(db, ctx, job)
        if not actual["materialized"]:
            continue
        for output in actual["outputs"]:
            generation = db.get(Generation, uuid.UUID(output["id"]))
            result.append(
                {
                    "id": str(generation.id),
                    "type": generation.output_type,
                    "blob_hash": generation.output_blob_hash,
                    "thumbnail_hash": generation.thumbnail_hash,
                    "job_id": str(job.id),
                }
            )
    return result


def _ui_blocks(db, rows):
    from app.modules.tools.generative_ui import validate_tree

    if not rows:
        return []
    result = []
    for attempt in db.scalars(
        select(ProductionAttempt)
        .where(
            ProductionAttempt.task_id.in_([t.id for t in rows]),
            ProductionAttempt.status == "succeeded",
        )
        .order_by(ProductionAttempt.created_at, ProductionAttempt.id)
    ):
        receipt = attempt.result or {}
        if (
            receipt.get("tool") == "present"
            and receipt.get("status") == "succeeded"
            and receipt.get("data", {}).get("presented") is True
        ):
            try:
                tree = validate_tree(attempt.operation["arguments"])
            except (KeyError, ValueError):
                continue
            result.append({"id": str(attempt.id), "version": 1, "tree": tree})
    return result[-20:]


def _project(db, message, run):
    from app.modules.harness import guidance as inbox

    current_guidance = (message.context or {}).get("guidance_id")
    rows = [
        t
        for t in service.tasks(db, run)
        if t.status != "superseded"
        and (
            not inbox.is_impact(t)
            or t.state["guidance_id"] == current_guidance
            or t.state["guidance_id"] == inbox.pending(run)
        )
    ]
    guidance_entry = next(
        (
            g
            for g in run.context.get("guidance_inbox", [])
            if g["id"] == (inbox.pending(run) or current_guidance)
        ),
        None,
    )
    outputs = _outputs(db, run, rows)
    questions = [
        {
            "task_id": str(t.id),
            "message": t.state.get("question", "需要补充信息"),
            "guidance_id": t.state.get("guidance_id"),
            "requires_confirmation": bool(t.state.get("guidance_confirmation")),
        }
        for t in rows
        if t.status == "waiting_input"
    ]
    errors = [t.error for t in rows if t.error and t.status in {"failed", "uncertain", "blocked"}]
    error = (run.error or next(iter(errors), {})).get("message")
    replies = [t.result["message"] for t in rows if t.result.get("message") and not t.parent_id]
    content = "\n\n".join(replies)
    if not content and run.status == "running":
        content = "\n\n".join(
            t.state.get("model_preview", "") for t in rows
            if t.status == "running" and not t.parent_id
        )
    if run.status == "needs_plan_approval":
        content = run.plan.get("summary", "制作方案已准备好，请确认后继续。")
    elif questions:
        content = "\n\n".join(q["message"] for q in questions)
    elif run.context.get("direct_request") and run.status == "succeeded" and outputs:
        label = "图片" if outputs[0]["type"] == "image" else "视频"
        mock = (
            run.context.get("conversation_request", {}).get("media", {}).get("provider") == "mock"
        )
        content = ("模拟测试：" if mock else "") + f"已生成{label}候选，可以预览或继续修改。"
    message.status = STATES.get(run.status, "failed")
    message.content = content
    message.error = error[:1000] if error else None
    message.progress = [
        {
            "id": str(t.id),
            "tool": t.specification.get("tool") or t.role,
            "message": t.goal,
            "status": t.status,
            "job_id": t.state.get("waiting_job_id"),
        }
        for t in rows
    ]
    message.context = {
        **(message.context or {}),
        "outputs": outputs,
        "ui_blocks": _ui_blocks(db, rows),
        "production": {
            **(message.context or {}).get("production", {}),
            "run_id": str(run.id),
            "revision": run.revision,
            "status": run.status,
            "is_current": True,
            "plan": run.plan if run.status == "needs_plan_approval" else None,
            "questions": questions,
            "guidance": {
                "id": guidance_entry["id"],
                "status": guidance_entry["status"],
                "decision": guidance_entry.get("decision"),
                "confirmation": {"expected_revision": run.revision}
                if guidance_entry["status"] == "awaiting_confirmation"
                else None,
            }
            if guidance_entry
            else None,
            "retryable": any(t.status == "failed" and t.error.get("retryable") for t in rows),
            "task_ids": [str(t.id) for t in rows],
        },
    }


def publish(db, run):
    """Update only the current guidance turn; immutable older history stays intact."""
    message = db.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == run.conversation_id,
            ConversationMessage.execution_kind == KIND,
            ConversationMessage.execution_id == run.id,
            ConversationMessage.role == "assistant",
        )
        .order_by(ConversationMessage.ordinal.desc())
        .limit(1)
    )
    if message and message.context.get("production", {}).get("is_current") is not False:
        _project(db, message, run)
    return message


def sync(db, conversation_id):
    ids = list(
        db.scalars(
            select(ConversationMessage.execution_id)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.execution_kind == KIND,
                ConversationMessage.role == "assistant",
                ConversationMessage.status.in_(["queued", "running", "needs_review", "paused"]),
            )
            .distinct()
        )
    )
    for run in db.scalars(select(ProductionRun).where(ProductionRun.id.in_(ids))):
        if run.conversation_id == conversation_id:
            publish(db, run)
    db.flush()


def read(db, ctx, message):
    from app.modules.conversation.service import message_out

    run = _owned_run(db, ctx, message.conversation_id, message.execution_id)
    publish(db, run)
    return message_out(message)


def control(db, ctx, message, data):
    run = _owned_run(db, ctx, message.conversation_id, message.execution_id, lock=True)
    if message.context.get("production", {}).get("is_current") is False:
        raise Conflict("这条消息后已有新的指导，请在最新制作进度中操作")
    if getattr(data, "additional_turns", 0):
        raise Conflict("请直接发送新的指导来继续制作")
    service.control(
        db,
        ctx,
        run.id,
        schemas.Control(
            request_key=uuid.uuid5(message.id, f"{run.revision}:{data.action}"),
            expected_revision=run.revision,
            action=data.action,
        ),
    )
    publish(db, run)


def retry(db, ctx, message, data):
    """Retry only explicitly recoverable steps of the saved run, never resubmit media."""
    run = _owned_run(db, ctx, message.conversation_id, message.execution_id, lock=True)
    if message.context.get("production", {}).get("is_current") is False:
        raise Conflict("请在最新制作进度中重试")
    service.control(
        db,
        ctx,
        run.id,
        schemas.Control(
            request_key=uuid.uuid5(message.conversation_id, str(data.request_key)),
            expected_revision=run.revision,
            action="retry",
        ),
    )
    publish(db, run)
