"""SQL-paginated task projection. Never load complete media or conversation histories."""

from sqlalchemy import String, case, cast, func, literal, or_, select, union_all

from app.models.agent import AgentRun
from app.models.canvas import Canvas, CanvasRun
from app.models.conversation import ConversationMessage
from app.models.generation import GenerationJob as Job
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex
from app.models.media_processing import MediaProcessingRun
from app.models.transcription import TranscriptionRun
from app.modules.generation.task_center import base_query

ACTIVE = ("pending", "submitted", "running")
ATTENTION = ("failed", "paused", "needs_review")


def normalized(column):
    return case(
        (column.in_(["queued", "uploading"]), "pending"),
        (
            column.in_(
                ["thinking", "processing", "waiting", "waiting_job", "probing", "transcoding"]
            ),
            "running",
        ),
        (column == "waiting_review", "needs_review"),
        (column.in_(["ready", "completed"]), "succeeded"),
        else_=column,
    )


def projection(model, kind, title, target_type, target_id, available, provider, operation):
    return select(
        model.id.label("id"),
        literal(kind).label("kind"),
        title.label("target_name"),
        literal(target_type).label("target_type"),
        target_id.label("target_id"),
        available.label("target_available"),
        normalized(model.status).label("status"),
        model.status.label("raw_status"),
        provider.label("provider"),
        operation.label("operation"),
        model.created_at.label("created_at"),
        model.updated_at.label("updated_at"),
        model.error.label("error"),
        literal("").label("request_type"),
        literal(None, String).label("model_name"),
        literal(0).label("result_count"),
        literal(False).label("has_warnings"),
    )


def queries(ctx):
    pid = ctx.project.id
    gen, title = base_query(pid)
    # Keep the generation source resolution's project isolation.
    cols = list(gen.selected_columns)
    gen = gen.with_only_columns(
        Job.id.label("id"),
        literal("generation").label("kind"),
        title.label("target_name"),
        Job.target_type.label("target_type"),
        Job.target_id.label("target_id"),
        cols[-2].label("target_available"),
        Job.status.label("status"),
        Job.status.label("raw_status"),
        Job.provider.label("provider"),
        func.coalesce(Job.input_snapshot["operation"].astext, "generate").label("operation"),
        Job.created_at,
        Job.updated_at,
        Job.error,
        Job.request_type,
        func.coalesce(
            Job.input_snapshot["routing"]["name"].astext,
            Job.input_snapshot["model_profile"]["display_name"].astext,
        ).label("model_name"),
        cols[-1].label("result_count"),
        case(
            (
                func.jsonb_typeof(Job.cost_raw["warnings"]) == "array",
                func.jsonb_array_length(Job.cost_raw["warnings"]) > 0,
            ),
            else_=False,
        ).label("has_warnings"),
    )
    private = (
        select(ConversationMessage.id)
        .where(
            ConversationMessage.execution_kind == "project_agent",
            ConversationMessage.execution_id == AgentRun.id,
        )
        .exists()
    )
    agent = projection(
        AgentRun,
        "agent",
        func.left(AgentRun.goal, 160),
        "agent_run",
        AgentRun.id,
        literal(True),
        literal("创作对话"),
        literal("agent"),
    ).where(AgentRun.project_id == pid, or_(AgentRun.created_by == ctx.user.id, ~private))
    canvas = (
        projection(
            CanvasRun,
            "canvas",
            Canvas.name,
            "canvas",
            Canvas.id,
            Canvas.id.is_not(None),
            literal("自由画布"),
            literal("canvas"),
        )
        .join(Canvas, (Canvas.id == CanvasRun.canvas_id) & (Canvas.project_id == pid))
        .where(CanvasRun.project_id == pid)
    )
    result = [gen, agent, canvas]
    for model, kind, provider in (
        (MediaVersion, "import", literal("素材入库")),
        (MediaIndex, "index", literal("画面索引")),
        (MediaProcessingRun, "processing", MediaProcessingRun.provider),
        (TranscriptionRun, "transcription", literal("转写与字幕")),
    ):
        q = projection(
            model,
            kind,
            LibraryMedia.name,
            "media",
            LibraryMedia.id,
            LibraryMedia.deleted_at.is_(None),
            provider,
            literal(kind),
        ).select_from(model)
        if model is not MediaVersion:
            q = q.join(MediaVersion, MediaVersion.id == model.version_id)
        q = q.join(LibraryMedia, LibraryMedia.id == MediaVersion.media_id).where(
            LibraryMedia.project_id == pid
        )
        if model is TranscriptionRun:
            q = q.where(TranscriptionRun.project_id == pid)
        result.append(q)
    return result


def catalog(db, ctx, status="", operation="", search="", offset=0, limit=24):
    tasks = union_all(*queries(ctx)).subquery()
    query = select(tasks)
    if operation:
        query = query.where(tasks.c.operation == operation)
    if search.strip():
        query = query.where(
            or_(
                tasks.c.target_name.icontains(search.strip(), autoescape=True),
                cast(tasks.c.id, String).icontains(search.strip(), autoescape=True),
            )
        )
    scope = query.subquery()
    counts = dict(db.execute(select(scope.c.status, func.count()).group_by(scope.c.status)).all())
    selected = (
        ACTIVE
        if status == "active"
        else ATTENTION
        if status == "attention"
        else (status,)
        if status
        else tuple(counts)
    )
    if status:
        query = query.where(tasks.c.status.in_(selected))
    rows = (
        db.execute(
            query.order_by(tasks.c.created_at.desc(), tasks.c.kind, tasks.c.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(r) for r in rows[:limit]],
        "counts": counts,
        "total": sum(counts.get(s, 0) for s in selected),
        "has_more": len(rows) > limit,
    }
