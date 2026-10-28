"""Direct media turns reuse generation jobs without depending on an agent engine."""

import uuid

from sqlalchemy import select

from app.core.errors import CapabilityUnsupported
from app.core.permissions import require
from app.models.conversation import ConversationMessage
from app.models.generation import Generation, GenerationJob
from app.modules.generation import jobs, workbench

STATES = {
    "pending": "queued",
    "queued": "queued",
    "submitted": "running",
    "running": "running",
    "succeeded": "completed",
    "failed": "failed",
    "canceled": "cancelled",
}


def start(db, ctx, conversation, message, data, *, retry_job_id=None):
    require(ctx.role, "generation.trigger")
    job = (
        jobs.retry(db, ctx, retry_job_id, defer_dispatch=True)
        if retry_job_id
        else workbench.create_job(
            db,
            ctx,
            workbench.Create(
                **data.media.model_dump(),
                prompt_override=data.content,
                request_key=uuid.uuid5(conversation.id, str(data.request_key)),
                work_id=conversation.work_id,
                skills=data.skills,
                count=1,
            ),
        )
    )
    message.execution_kind = "generation"
    message.execution_id = job.id
    message.context = {
        "mode": data.mode,
        "model_provider": data.model_provider,
        "scope": [],
        "skills": [s.model_dump(mode="json") for s in data.skills],
        "media": data.media.model_dump(mode="json"),
    }
    return job


def sync(db, conversation_id):
    rows = db.execute(
        select(ConversationMessage, GenerationJob)
        .join(GenerationJob, GenerationJob.id == ConversationMessage.execution_id)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.execution_kind == "generation",
            ConversationMessage.role == "assistant",
            ConversationMessage.status.in_(["queued", "running"]),
        )
    )
    for message, job in rows:
        publish(db, message, job)
    db.flush()


def publish(db, message, job):
    outputs = list(db.scalars(select(Generation).where(Generation.job_id == job.id)))
    message.status = STATES.get(job.status, "failed")
    message.error = job.error[:1000] if job.error else None
    message.context = {
        **message.context,
        "outputs": [
            {
                "id": str(g.id),
                "type": g.output_type,
                "blob_hash": g.output_blob_hash,
                "thumbnail_hash": g.thumbnail_hash,
            }
            for g in outputs
            if g.output_blob_hash
        ],
    }
    kind = "图片" if job.request_type == "image" else "视频"
    prefix = "模拟测试：" if job.provider == "mock" else ""
    message.content = (
        f"{prefix}已生成{kind}，可以预览、继续讨论或作为下一次创作的参考。"
        if job.status == "succeeded"
        else ""
    )


def control(db, ctx, message, data):
    require(ctx.role, "generation.trigger")
    if data.action != "cancel":
        raise CapabilityUnsupported("生成任务不能暂停；失败后可调整要求，再发送一次")
    job = jobs.cancel(db, ctx, message.execution_id)
    publish(db, message, job)
