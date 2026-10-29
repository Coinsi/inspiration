"""Ownership, ordering and history are business rules, independent of execution."""

import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core import audit
from app.core.config import settings
from app.core.errors import Conflict, NotFound
from app.models.conversation import Conversation, ConversationMessage
from app.models.identity import Project
from app.modules.conversation import execution, inputs

ACTIVE = {"queued", "running", "needs_review", "paused"}


def get(db, ctx, conversation_id, lock=False):
    q = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.project_id == ctx.project.id,
        Conversation.created_by == ctx.user.id,
    )
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    conversation = db.scalar(q)
    if not conversation:
        raise NotFound("对话不存在或不属于当前账号")
    return conversation


def summary(c):
    return {
        "id": c.id,
        "title": c.title,
        "work_id": c.work_id,
        "archived": c.archived_at is not None,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def message_out(m):
    kind = "generation" if m.execution_kind == "generation" else "agent"
    return {
        "id": m.id,
        "ordinal": m.ordinal,
        "role": m.role,
        "content": m.content,
        "status": m.status,
        "context": m.context,
        "progress": m.progress,
        "error": m.error,
        "created_at": m.created_at,
        "task_id": m.execution_id,
        "execution_kind": m.execution_kind,
        "task_path": (
            f"workbench?conversation={m.conversation_id}&message={m.id}&ordinal={m.ordinal}"
            if m.execution_kind == "production"
            else f"tasks?kind={kind}&task={m.execution_id}"
            if m.execution_id
            else None
        ),
    }


def create(db, ctx, data):
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    prior = db.get(Conversation, data.id)
    if prior:
        return summary(get(db, ctx, data.id))
    if data.work_id:
        from app.modules.conversation.works import get as get_work

        get_work(db, ctx.project.id, data.work_id)
    c = Conversation(
        id=data.id,
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        title=data.title,
        work_id=data.work_id,
    )
    db.add(c)
    db.flush()
    return summary(c)


def update(db, ctx, conversation_id, data):
    c = get(db, ctx, conversation_id, True)
    from app.modules.conversation.media import sync

    sync(db, c.id)
    if "work_id" in data.model_fields_set and data.work_id != c.work_id:
        if db.scalar(
            select(ConversationMessage.id)
            .where(
                ConversationMessage.conversation_id == c.id,
                ConversationMessage.status.in_(ACTIVE),
            )
            .limit(1)
        ):
            raise Conflict("请先结束当前任务，再更换作品归属")
        if data.work_id:
            from app.modules.conversation.works import get as get_work

            get_work(db, ctx.project.id, data.work_id)
        c.work_id = data.work_id
    if data.title is not None:
        c.title = data.title
    if data.archived is not None:
        c.archived_at = datetime.now(UTC) if data.archived else None
    c.updated_at = datetime.now(UTC)
    db.flush()
    return summary(c)


def detail(db, ctx, conversation_id, before=None):
    c = get(db, ctx, conversation_id)
    from app.modules.conversation.media import sync

    sync(db, c.id)
    from app.modules.conversation.production import sync as sync_production

    sync_production(db, c.id)
    q = select(ConversationMessage).where(ConversationMessage.conversation_id == c.id)
    if before is not None:
        q = q.where(ConversationMessage.ordinal < before)
    rows = list(db.scalars(q.order_by(ConversationMessage.ordinal.desc()).limit(101)))
    more = len(rows) > 100
    messages = list(reversed(rows[:100]))
    return {**summary(c), "messages": [message_out(m) for m in messages], "has_older": more}


def history_snapshot(messages):
    history, budget = [], 24000
    for m in reversed(messages):
        content = m.content or m.error or ""
        ui = (m.context or {}).get("ui_blocks", [])
        if not content and not ui:
            continue
        ui_text = json.dumps(ui, ensure_ascii=False) if ui else ""
        if len(content) + len(ui_text) > budget:
            break
        item = {"role": m.role, "content": content, "status": m.status}
        if ui:
            item["ui_blocks"] = ui
        if (m.context or {}).get("outputs"):
            item["media_outputs"] = m.context["outputs"]
            item["media_note"] = "这些是生成素材引用，未附画面或音轨内容，不能声称已看过。"
        for key in ("attachments", "references"):
            if (m.context or {}).get(key) and m.role == "user":
                item[key] = m.context[key]
        cost = len(json.dumps(item, ensure_ascii=False))
        if cost > budget:
            break
        history.append(item)
        budget -= cost
    return {"messages": list(reversed(history)), "history_truncated": len(history) < len(messages)}


def send(db, ctx, conversation_id, data, *, retry_job_id=None):
    # Same lock order as agent.create; serializes sends without relying on UI.
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    c = get(db, ctx, conversation_id, True)
    from app.modules.conversation import media

    media.sync(db, c.id)
    from app.modules.conversation import production

    production.sync(db, c.id)
    query = select(ConversationMessage).where(ConversationMessage.conversation_id == c.id)
    prior = db.scalar(
        query.where(
            ConversationMessage.request_key == data.request_key,
            ConversationMessage.role == "user",
        )
    )
    if prior:
        expected = {
            "mode": data.mode,
            "model_provider": data.model_provider,
            "scope": [item.model_dump(mode="json") for item in data.scope],
            "skills": [item.model_dump(mode="json") for item in data.skills],
            "media": data.media.model_dump(mode="json") if data.media else None,
            "production_request": data.production.model_dump(mode="json")
            if data.production
            else None,
            **inputs.request_context(data),
        }
        if prior.content != data.content or any(
            prior.context.get(k, [] if k in {"attachment_requests", "reference_requests"} else None)
            != v
            for k, v in expected.items()
        ):
            raise Conflict("消息已发送，请使用新的请求提交不同内容或配置")
        return detail(db, ctx, c.id)
    if c.archived_at:
        raise Conflict("请先恢复这段对话再继续发送")
    if db.scalar(
        query.where(
            ConversationMessage.role == "assistant",
            ConversationMessage.status.in_(ACTIVE),
            ConversationMessage.execution_kind != "production",
        ).limit(1)
    ):
        raise Conflict("这段对话还有未结束的任务，请先审阅、继续或停止任务")
    # Bound loading as well as model context, even after thousands of turns.
    rows = list(
        reversed(list(db.scalars(query.order_by(ConversationMessage.ordinal.desc()).limit(100))))
    )
    ordinal = rows[-1].ordinal + 1 if rows else 1
    history = history_snapshot(rows)
    history["history_truncated"] |= bool(rows and rows[0].ordinal > 1)
    user = ConversationMessage(
        conversation_id=c.id,
        ordinal=ordinal,
        role="user",
        content=data.content,
        request_key=data.request_key,
        status="completed",
    )
    reply = ConversationMessage(
        conversation_id=c.id,
        ordinal=ordinal + 1,
        role="assistant",
        content="",
        request_key=data.request_key,
        status="queued",
    )
    db.add_all([user, reply])
    db.flush()
    execution_data, current_inputs, input_context = inputs.bind_inputs(db, ctx, c, user, data)
    history["current_attachments"] = current_inputs
    job = None
    if retry_job_id:
        job = media.start(db, ctx, c, reply, execution_data, retry_job_id=retry_job_id)
    elif settings.production_harness_enabled or data.production:
        production.start(db, ctx, c, reply, execution_data, history)
    elif data.media:
        job = media.start(db, ctx, c, reply, execution_data)
    else:
        execution.start(db, ctx, c, reply, execution_data, history)
    reply.context = {**reply.context, **input_context}
    user.context = dict(reply.context)
    if not rows and c.title == "新的对话":
        c.title = data.content.replace("\n", " ")[:48]
    c.updated_at = datetime.now(UTC)
    db.flush()
    audit.record(
        db,
        action="conversation.message",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="conversation",
        target_id=c.id,
        detail={"message_id": str(user.id), "execution_id": str(reply.execution_id)},
    )
    db.flush()
    if job is not None:
        from app.modules.generation.jobs import dispatch

        dispatch(db, job)
    return detail(db, ctx, c.id)


def retry_input(db, ctx, conversation_id, message_id, *, allow_resume=False, allow_completed=False):
    c = get(db, ctx, conversation_id)
    from app.modules.conversation.media import sync

    sync(db, c.id)
    from app.modules.conversation.production import sync as sync_production

    sync_production(db, c.id)
    reply = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == message_id,
            ConversationMessage.conversation_id == c.id,
            ConversationMessage.role == "assistant",
        )
    )
    if not reply:
        raise NotFound("消息不存在")
    restore_completed = (
        allow_completed
        and not allow_resume
        and reply.status == "completed"
        and reply.execution_kind in {"production", "generation"}
        and bool((reply.context or {}).get("outputs"))
    )
    if reply.execution_kind == "production" and not restore_completed:
        raise Conflict("请在制作进度中重试失败步骤或提出修改意见，避免重复提交付费任务")
    if not restore_completed and reply.status not in {"failed", "cancelled"}:
        raise Conflict("只有失败或已停止的任务可以重试")
    if reply.execution_kind == "generation" and not restore_completed:
        from app.models.generation import GenerationJob
        from app.modules.generation.model_routing import retry_allowed

        job = db.get(GenerationJob, reply.execution_id)
        if not job or job.project_id != ctx.project.id or not retry_allowed(job):
            raise Conflict("上游提交状态不确定，已停止重发；请先核对上游任务记录")
        if not allow_resume and (
            (job.routing_state or {}).get("resume")
            or (job.routing_state or {}).get("phase") in {"accepted", "generated"}
        ):
            raise Conflict("上游已接收此任务，请使用重试继续接收原结果，不能按新参数重新提交")
    source = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == c.id,
            ConversationMessage.request_key == reply.request_key,
            ConversationMessage.role == "user",
        )
    )
    if not source:
        raise NotFound("原始创作要求不存在")
    context = source.context or {}
    if restore_completed and not context.get("media"):
        raise Conflict("这条消息没有可恢复的原始生成要求，请直接提出修改意见")
    return {
        "content": source.content,
        "mode": context.get("mode", "standard"),
        "model_provider": context.get("model_provider"),
        "scope": context.get("scope", []),
        "skills": context.get("skills", []),
        "media": context.get("media"),
        "attachments": context.get("attachment_requests", []),
        "references": context.get("reference_requests", []),
    }


def retry(db, ctx, conversation_id, message_id, data):
    from app.modules.conversation.schemas import Send

    c = get(db, ctx, conversation_id)
    reply = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == message_id,
            ConversationMessage.conversation_id == c.id,
            ConversationMessage.role == "assistant",
        )
    )
    if reply and reply.execution_kind == "production":
        from app.modules.conversation.production import retry as retry_production

        retry_production(db, ctx, reply, data)
        return detail(db, ctx, c.id)

    payload = retry_input(db, ctx, conversation_id, message_id, allow_resume=True)
    return send(
        db,
        ctx,
        conversation_id,
        Send(**payload, request_key=data.request_key),
        retry_job_id=reply.execution_id if reply and reply.execution_kind == "generation" else None,
    )


def control(db, ctx, conversation_id, message_id, data):
    c = get(db, ctx, conversation_id, True)
    m = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == message_id,
            ConversationMessage.conversation_id == c.id,
            ConversationMessage.role == "assistant",
        )
    )
    if not m or not m.execution_id:
        raise NotFound("执行任务不存在")
    latest = db.scalar(
        select(func.max(ConversationMessage.ordinal)).where(
            ConversationMessage.conversation_id == c.id
        )
    )
    if data.action == "resume" and (
        c.archived_at or (m.execution_kind != "production" and m.ordinal != latest)
    ):
        raise Conflict("只能在未归档的对话中继续最后一条任务，旧任务请重新描述需求")
    if m.execution_kind == "production":
        from app.modules.conversation.production import control as production_control

        production_control(db, ctx, m, data)
    elif m.execution_kind == "generation":
        from app.modules.conversation.media import control as media_control

        media_control(db, ctx, m, data)
    else:
        execution.control(db, ctx, m, data)
    return message_out(m)
