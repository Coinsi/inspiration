"""Ownership, ordering and history are business rules, independent of execution."""

import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core import audit
from app.core.errors import Conflict, NotFound
from app.models.conversation import Conversation, ConversationMessage
from app.models.identity import Project
from app.modules.conversation import execution

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
        "task_path": f"tasks?kind={kind}&task={m.execution_id}" if m.execution_id else None,
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
        history.append(item)
        budget -= len(content) + len(ui_text)
    return {"messages": list(reversed(history)), "history_truncated": len(history) < len(messages)}


def send(db, ctx, conversation_id, data):
    # Same lock order as agent.create; serializes sends without relying on UI.
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    c = get(db, ctx, conversation_id, True)
    from app.modules.conversation import media

    media.sync(db, c.id)
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
        }
        if prior.content != data.content or any(
            prior.context.get(k) != v for k, v in expected.items()
        ):
            raise Conflict("消息已发送，请使用新的请求提交不同内容或配置")
        return detail(db, ctx, c.id)
    if c.archived_at:
        raise Conflict("请先恢复这段对话再继续发送")
    if db.scalar(
        query.where(
            ConversationMessage.role == "assistant", ConversationMessage.status.in_(ACTIVE)
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
    job = None
    if data.media:
        job = media.start(db, ctx, c, reply, data)
    else:
        execution.start(db, ctx, c, reply, data, history)
    user.context = reply.context
    db.add_all([user, reply])
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


def retry_input(db, ctx, conversation_id, message_id):
    c = get(db, ctx, conversation_id)
    from app.modules.conversation.media import sync

    sync(db, c.id)
    reply = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == message_id,
            ConversationMessage.conversation_id == c.id,
            ConversationMessage.role == "assistant",
        )
    )
    if not reply:
        raise NotFound("消息不存在")
    if reply.status not in {"failed", "cancelled"}:
        raise Conflict("只有失败或已停止的任务可以重试")
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
    return {
        "content": source.content,
        "mode": context.get("mode", "standard"),
        "model_provider": context.get("model_provider"),
        "scope": context.get("scope", []),
        "skills": context.get("skills", []),
        "media": context.get("media"),
    }


def retry(db, ctx, conversation_id, message_id, data):
    from app.modules.conversation.schemas import Send

    payload = retry_input(db, ctx, conversation_id, message_id)
    return send(db, ctx, conversation_id, Send(**payload, request_key=data.request_key))


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
    if data.action == "resume" and (c.archived_at or m.ordinal != latest):
        raise Conflict("只能在未归档的对话中继续最后一条任务，旧任务请重新描述需求")
    if m.execution_kind == "generation":
        from app.modules.conversation.media import control as media_control

        media_control(db, ctx, m, data)
    else:
        execution.control(db, ctx, m, data)
    return message_out(m)
