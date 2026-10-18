"""Adapter boundary: conversation API does not expose an agent framework's states."""

import uuid

from sqlalchemy import select

from app.models.agent import AgentRun
from app.models.conversation import ConversationMessage
from app.modules.agent import schemas as agent_schemas
from app.modules.agent import service as agent

STATES = {
    "queued": "queued",
    "thinking": "running",
    "running": "running",
    "waiting_job": "running",
    "waiting_review": "needs_review",
    "paused": "paused",
    "succeeded": "completed",
    "failed": "failed",
    "canceled": "cancelled",
}


def start(db, ctx, conversation, message, data, history):
    work_context = None
    if conversation.work_id:
        from app.modules.conversation.works import get as get_work

        work = get_work(db, ctx.project.id, conversation.work_id)
        work_context = {
            "id": str(work.id), "title": work.title,
            "script_id": str(work.script_id) if work.script_id else None,
            "engineering_revision": work.engineering_revision,
        }
    # Namespaced idempotency prevents a key reused in another conversation from
    # accidentally attaching somebody else's execution.
    key = uuid.uuid5(conversation.id, str(data.request_key))
    result = agent.create(
        db,
        ctx,
        agent_schemas.Create(
            goal=data.content,
            engine="mock" if data.mode == "test" else "cloud_llm",
            model_provider=None if data.mode == "test" else data.model_provider,
            scope=data.scope,
            skills=[s.id for s in data.skills],
            skill_versions=data.skills,
            request_key=key,
        ),
    )
    run = db.get(AgentRun, result["id"])
    run.input_context = {"conversation_id": str(conversation.id), "work": work_context, **history}
    message.execution_kind = "project_agent"
    message.execution_id = run.id
    message.context = {
        "work": work_context,
        "mode": data.mode,
        "model_provider": data.model_provider,
        "history_truncated": history["history_truncated"],
        "scope": [s.model_dump(mode="json") for s in data.scope],
        "skills": [s.model_dump(mode="json") for s in data.skills],
    }


def publish(db, run):
    """Persist portable message content and progress in the application's store."""
    message = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.execution_kind == "project_agent",
            ConversationMessage.execution_id == run.id,
        )
    )
    if not message:
        return
    message.status = STATES.get(run.status, "failed")
    message.content = run.result or ""
    message.error = run.error
    message.progress = [
        {
            "id": s["id"],
            "tool": s["tool"],
            "message": s.get("message", ""),
            "status": s["status"],
            "job_id": s.get("job_id"),
        }
        for s in (run.steps or [])
    ]


def control(db, ctx, message, data):
    result = agent.control(
        db, ctx, message.execution_id, agent_schemas.Control(**data.model_dump())
    )
    publish(db, db.get(AgentRun, result["id"]))
