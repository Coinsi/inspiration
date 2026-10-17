"""User-facing results and recovery affordances; existing commands enforce permissions."""

import uuid
from urllib.parse import quote

from sqlalchemy import select

from app.core.errors import AppError, NotFound
from app.core.permissions import can
from app.models.agent import AgentRun
from app.models.canvas import CanvasRun
from app.models.conversation import Conversation, ConversationMessage
from app.models.identity import AuditLog
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex
from app.models.media_processing import MediaProcessingRun
from app.models.transcription import TranscriptionRun
from app.modules.activity.catalog import queries

MODELS = {
    "agent": AgentRun,
    "canvas": CanvasRun,
    "import": MediaVersion,
    "index": MediaIndex,
    "processing": MediaProcessingRun,
    "transcription": TranscriptionRun,
}
KINDS = ["generation", "agent", "canvas", "import", "index", "processing", "transcription"]
LABELS = {"pause": "暂停", "resume": "继续执行", "cancel": "停止任务", "retry": "重试未完成部分"}


def object_link(db, ctx, kind, object_id):
    from app.modules.assist.targets import get_adapter

    try:
        obj, state = get_adapter(kind).read(db, ctx.project.id, uuid.UUID(str(object_id)))
    except (AppError, ValueError, TypeError):
        return {"label": "原对象已不可用", "path": None}
    root = f"/projects/{ctx.project.id}"
    path = {
        "script": f"/scripts/{obj.id}",
        "shot": f"/shots?shot={obj.id}",
        "asset": f"/assets/{obj.id}",
        "scene": f"/scripts/{getattr(obj, 'script_id', '')}",
    }.get(kind)
    return {
        "label": getattr(obj, "title", None)
        or getattr(obj, "name", None)
        or getattr(obj, "code", kind),
        "path": root + path if path else None,
    }


def history(db, ctx, target_id):
    # Never expose raw audit details (provider configuration may contain sensitive data).
    labels = {
        "agent.approve": "已批准提议",
        "agent.reject": "已拒绝提议",
        "agent.pause": "已暂停",
        "agent.resume": "已继续",
        "agent.cancel": "已停止",
        "canvas.pause": "已暂停",
        "canvas.resume": "已继续",
        "canvas.retry": "已重试失败节点",
        "canvas.cancel": "已停止",
        "transcription.edit": "已保存字幕草稿",
        "transcription.publish": "已发布字幕到检索",
        "transcription.retry": "已续接转写",
        "transcription.cancel": "已请求停止转写",
        "library.processing.retry": "已重试素材处理",
        "library.processing.cancel": "已请求停止处理",
        "library.retry": "已重试素材入库",
    }
    rows = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.project_id == ctx.project.id,
            AuditLog.target_id == target_id,
            AuditLog.action.in_(labels),
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(31)
    ).all()
    return [
        {"id": str(a.id), "label": labels[a.action], "at": a.created_at} for a in rows[:30]
    ], len(rows) > 30


def detail(db, ctx, kind, task_id):
    if kind not in MODELS:
        raise NotFound("任务不存在")
    q = queries(ctx)[KINDS.index(kind)]
    model = MODELS[kind]
    row = db.execute(q.where(model.id == task_id)).mappings().first()
    if not row:
        raise NotFound("任务不存在")
    run = db.get(model, task_id)
    root = f"/projects/{ctx.project.id}"
    result = {
        "summary": dict(row),
        "source": None,
        "result_note": "",
        "scope": [],
        "actions": [],
        "changes": [],
        "children": [],
        "progress": None,
        "recovery_note": "",
    }
    result["history"], result["more_history"] = history(db, ctx, task_id)

    def action(name, path, payload=None, note=""):
        result["actions"].append(
            {
                "name": name,
                "label": LABELS[name],
                "path": root + path,
                "payload": payload,
                "note": note,
            }
        )

    def child(job_id):
        from app.models.generation import GenerationJob

        try:
            job = db.get(GenerationJob, uuid.UUID(str(job_id)))
        except (TypeError, ValueError):
            return
        if job and job.project_id == ctx.project.id:
            result["children"].append({"id": str(job.id), "status": job.status})

    if kind == "agent":
        from app.modules.agent.service import owner

        try:
            owner(ctx, run)
            editable = True
        except AppError:
            editable = False
        message = db.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.execution_kind == "project_agent",
                ConversationMessage.execution_id == run.id,
            )
            .limit(1)
        )
        resumable = True
        if message:
            conversation = db.get(Conversation, message.conversation_id)
            result["source"] = {
                "label": "回到发起对话",
                "path": f"{root}/workbench?conversation={conversation.id}",
            }
            newer = db.scalar(
                select(ConversationMessage.id)
                .where(
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.ordinal > message.ordinal,
                )
                .limit(1)
            )
            resumable = not conversation.archived_at and not newer
        else:
            result["source"] = {"label": "查看执行与审阅", "path": f"{root}/agent?run={run.id}"}
        result["result_note"] = run.result or (
            "提议等待确认，尚未应用。"
            if run.status == "waiting_review"
            else "执行记录已保存；完成后的回复会保留在发起对话中。"
        )
        result["scope"] = [
            object_link(db, ctx, t.get("target_type"), t.get("target_id")) for t in run.scope
        ]
        result["recovery_note"] = "继续执行会保留已完成步骤；停止任务不会撤销已经批准的修改。"
        if not resumable:
            result["recovery_note"] = (
                "这是旧任务或已归档对话，请回到原对话查看记录，另发新需求继续。"
            )
        for step in run.steps:
            if step.get("job_id"):
                child(step["job_id"])
            if step.get("tool") != "object.propose_edit":
                continue
            args = step.get("arguments") or {}
            preview = step.get("result") or {}
            result["changes"].append(
                {
                    "id": step["id"],
                    "label": args.get("summary") or "内容修改",
                    "state": "applied"
                    if step.get("decision") == "approve" and step.get("applied")
                    else "rejected"
                    if step.get("decision") == "reject"
                    else "proposed",
                    "target": object_link(db, ctx, args.get("target_type"), args.get("target_id")),
                    "before": preview.get("before"),
                    "after": preview.get("after"),
                }
            )
        if editable:
            for name, states in (
                ("pause", ("queued", "thinking", "running", "waiting_job")),
                (
                    "cancel",
                    ("queued", "thinking", "running", "waiting_job", "waiting_review", "paused"),
                ),
            ):
                if run.status in states:
                    action(
                        name,
                        f"/agent/runs/{run.id}/control",
                        {"action": name},
                        "已发出的云端请求可能仍运行和计费。" if name == "cancel" else "",
                    )
            if (
                resumable
                and run.engine != "external"
                and run.status in ("paused", "failed")
                and run.turns < run.max_turns
            ):
                action(
                    "resume",
                    f"/agent/runs/{run.id}/control",
                    {"action": "resume"},
                    "从已保存的位置继续，后续模型调用可能产生费用。",
                )
            elif resumable and run.status in ("paused", "failed") and run.turns >= run.max_turns:
                result["recovery_note"] = (
                    "本次执行步数已用完，请回到对话检查结果并决定是否追加步数。"
                )
    elif kind == "canvas":
        result["source"] = {"label": "回到画布", "path": f"{root}/canvas?canvas={run.canvas_id}"}
        result["result_note"] = (
            f"按画布第 {run.revision} 版运行；节点产出随任务保存，可从下方关联任务查看。"
        )
        result["scope"] = [
            {"label": f"本次画布 · 第 {run.revision} 版", "path": result["source"]["path"]}
        ]
        result["progress"] = {
            "completed": sum(s.get("status") == "succeeded" for s in run.steps.values()),
            "total": len(run.steps),
        }
        result["recovery_note"] = "重试只重新执行失败节点，保留成功节点；停止不会删除已有结果。"
        for step in run.steps.values():
            if step.get("job_id"):
                child(step["job_id"])
            for previous in step.get("previous_jobs", []):
                child(previous)
        if can(ctx.role, "generation.trigger"):
            for name, states in {
                "pause": ("running",),
                "resume": ("paused",),
                "cancel": ("running", "paused", "failed"),
                "retry": ("failed",),
            }.items():
                if run.status in states:
                    action(
                        name,
                        f"/canvases/{run.canvas_id}/runs/{run.id}/control",
                        {"action": name},
                        "生成可能产生供应商费用；已发出的请求可能继续计费。",
                    )
    else:
        version = run if kind == "import" else db.get(MediaVersion, run.version_id)
        media = db.get(LibraryMedia, version.media_id)
        result["source"] = {
            "label": "查看素材与处理记录",
            "path": f"{root}/materials?media={media.id}&version={version.id}&return="
            + quote(f"{root}/tasks?kind={kind}&task={task_id}", safe=""),
        }
        result["scope"] = [
            {"label": f"{media.name} · 版本 {version.ordinal}", "path": result["source"]["path"]}
        ]
        editable = can(ctx.role, "asset.edit") and media.deleted_at is None
        result["recovery_note"] = "处理结果属于这份素材的指定版本，不会替换原文件。"
        if kind == "transcription":
            result["source"] = {
                "label": "查看字幕草稿",
                "path": f"{root}/transcriptions?version={version.id}&run={run.id}",
            }
            result["result_note"] = (
                (
                    "字幕已发布到检索。"
                    if run.published_revision == run.revision
                    else "字幕保存在转写草稿中；发布后才会用于字幕检索。"
                )
                if run.status == "ready"
                else "已完成的识别片段会保留，失败后可以续接。"
            )
            if editable:
                if run.status in ("queued", "processing") and not run.cancel_requested:
                    action("cancel", f"/transcriptions/{run.id}/control", {"action": "cancel"})
                if run.status in ("failed", "canceled"):
                    action("retry", f"/transcriptions/{run.id}/control", {"action": "retry"})
        elif kind == "processing":
            result["result_note"] = (
                "远端清理结果保存在素材处理记录中。"
                if run.operation == "delete"
                else "预览或索引保存在素材处理记录中，是否可检索以对应能力的处理结果为准。"
            )
            if editable:
                if run.status == "failed":
                    action("retry", f"/library/processing/runs/{run.id}/retry")
                if (
                    run.operation != "delete"
                    and run.status in ("queued", "processing", "waiting")
                    and not run.cancel_requested
                ):
                    action("cancel", f"/library/processing/runs/{run.id}/cancel")
        elif kind == "index":
            result["result_note"] = (
                "画面索引属于此版本，完成后用于素材检索；不代表声音、字幕索引也已完成。"
            )
            if editable and run.status in ("queued", "processing") and not run.cancel_requested:
                action("cancel", f"/library/indices/{run.id}/cancel")
            if editable and run.status in ("failed", "canceled"):
                action(
                    "retry",
                    f"/library/versions/{version.id}/index",
                    note="按当前索引模型继续或新建索引，已完成的兼容索引会复用。",
                )
        else:
            result["result_note"] = (
                "原文件与预览保存在素材库；预览就绪不等于语义、字幕或声音索引已完成。"
            )
            if editable and run.status == "failed":
                action("retry", f"/library/versions/{version.id}/retry")
            if run.status == "uploading":
                result["recovery_note"] = "文件仍在上传，需回到上传入口并选择同一原文件续传。"
        if kind in ("index", "transcription"):
            result["progress"] = {"completed": run.completed, "total": run.total}
        if getattr(run, "cancel_requested", False) and run.status not in (
            "ready",
            "failed",
            "canceled",
        ):
            result["recovery_note"] = "已请求停止，正在等待当前处理步骤结束。"
        if media.deleted_at:
            result["source"] = {"label": "前往回收站", "path": f"{root}/materials?tab=trash"}
            result["recovery_note"] = "素材已在回收站，处理记录保留；恢复素材后再操作。"
    result["children"] = list({j["id"]: j for j in result["children"]}.values())
    return result
