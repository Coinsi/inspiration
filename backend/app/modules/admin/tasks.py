"""Bounded cross-project metadata queries. No prompts, private filenames or raw errors."""

import re
import uuid

from sqlalchemy import String, cast, func, literal, or_, select, union_all

from app.core.errors import NotFound
from app.models.attachment import Attachment as ConversationAttachment
from app.models.engineering import EngineeringArchiveJob
from app.models.generation import GenerationJob
from app.models.harness import ProductionRun
from app.models.identity import Membership, Project, User
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex
from app.models.media_processing import MediaProcessingRun
from app.models.transcription import TranscriptionRun
from app.models.work import CreativeWork

MODELS = {
    "generation": GenerationJob,
    "production": ProductionRun,
    "upload": MediaVersion,
    "attachment": ConversationAttachment,
    "index": MediaIndex,
    "processing": MediaProcessingRun,
    "transcription": TranscriptionRun,
    "archive": EngineeringArchiveJob,
}
ACTIVE = (
    "pending",
    "queued",
    "submitted",
    "running",
    "processing",
    "waiting",
    "uploading",
    "probing",
)
ATTENTION = (
    "failed",
    "needs_attention",
    "needs_review",
    "needs_plan_approval",
    "uncertain",
    "submission_unknown",
)


def query(kind):
    model = MODELS[kind]
    project = getattr(model, "project_id", None)
    owner = getattr(model, "created_by", None)
    q = select(
        literal(kind).label("kind"), model.id, model.status, model.created_at, model.updated_at
    )
    if kind in {"upload", "index", "processing"}:
        if kind != "upload":
            q = q.select_from(model).join(MediaVersion, model.version_id == MediaVersion.id)
        q = q.join(LibraryMedia, MediaVersion.media_id == LibraryMedia.id)
        project = LibraryMedia.project_id
    elif kind == "archive":
        q = q.select_from(model).join(CreativeWork, model.work_id == CreativeWork.id)
        project = CreativeWork.project_id
    return q.add_columns(
        cast(project, String).label("project_id"),
        (cast(owner, String) if owner is not None else literal(None, String)).label("user_id"),
        cast(
            getattr(model, "request_type", getattr(model, "operation", literal(""))), String
        ).label("operation"),
    )


def describe(db, rows):
    """Resolve display metadata in batches, never expose private task inputs."""
    project_ids = {uuid.UUID(str(r["project_id"])) for r in rows if r.get("project_id")}
    user_ids = {uuid.UUID(str(r["user_id"])) for r in rows if r.get("user_id")}
    projects = (
        {str(p.id): p.name for p in db.scalars(select(Project).where(Project.id.in_(project_ids)))}
        if project_ids
        else {}
    )
    users = (
        {
            str(u.id): (u.display_name or u.username)
            for u in db.scalars(select(User).where(User.id.in_(user_ids)))
        }
        if user_ids
        else {}
    )
    labels = {
        "generation": "生成媒体",
        "production": "执行创作流程",
        "upload": "上传素材",
        "attachment": "上传对话附件",
        "index": "建立素材检索索引",
        "processing": "处理媒体素材",
        "transcription": "转写音视频字幕",
        "archive": "导出作品工程",
    }
    for r in rows:
        r["project_name"] = projects.get(str(r.get("project_id")), "项目已不存在")
        r["user_name"] = users.get(
            str(r.get("user_id")), "用户已不存在" if r.get("user_id") else "未记录发起人"
        )
        r["title"] = labels.get(r.get("kind"), "任务")
        if r.get("kind") == "generation":
            r["title"] = {"image": "生成图片", "video": "生成视频", "audio": "生成音频"}.get(
                r.get("operation"), "生成媒体 / 渲染"
            )
        if r.get("kind") == "archive" and r.get("operation") == "import":
            r["title"] = "导入作品工程"
    return rows


def table(kind=""):
    return (query(kind) if kind else union_all(*(query(k) for k in MODELS))).subquery()


def filtered(kind="", state="", user_id=None, project_id=None, search=""):
    t = table(kind)
    conditions = []
    if state == "active":
        conditions.append(t.c.status.in_(ACTIVE))
    elif state == "attention":
        conditions.append(t.c.status.in_(ATTENTION))
    elif state:
        conditions.append(t.c.status == state)
    if user_id:
        conditions.append(t.c.user_id == str(user_id))
    if project_id:
        conditions.append(t.c.project_id == str(project_id))
    if search.strip():
        term = search.strip()
        conditions.append(
            or_(
                cast(t.c.id, String).icontains(term, autoescape=True),
                t.c.project_id.in_(
                    select(cast(Project.id, String)).where(
                        Project.name.icontains(term, autoescape=True)
                    )
                ),
                t.c.user_id.in_(
                    select(cast(User.id, String)).where(
                        or_(
                            User.display_name.icontains(term, autoescape=True),
                            User.username.icontains(term, autoescape=True),
                        )
                    )
                ),
            )
        )
    return t, conditions


def listing(db, *, offset=0, limit=25, **filters):
    t, conditions = filtered(**filters)
    total = db.scalar(select(func.count()).select_from(t).where(*conditions))
    rows = db.execute(
        select(t)
        .where(*conditions)
        .order_by(t.c.created_at.desc(), t.c.kind, t.c.id)
        .offset(offset)
        .limit(limit)
    ).mappings()
    return {"items": describe(db, [dict(r) for r in rows]), "total": total}


def diagnosis(error, status):
    raw = str(error or "").lower()
    code = re.search(r"(?:http[ /:]|status(?:_code)?[\s=:]+)([45]\d\d)", raw)
    code = code.group(1) if code else None
    if "unknown" in raw or "uncertain" in raw or "无法确认" in raw:
        message = "上游受理状态不明，先核对原任务回执，不要重新提交。"
    elif code in {"401", "403"}:
        message = "服务鉴权或权限异常，请检查对应模型服务。"
    elif code == "429" or "rate limit" in raw:
        message = "上游限流或额度不足，请检查渠道状态与限额。"
    elif "timeout" in raw or "超时" in raw:
        message = "连接或任务等待超时，请核对服务状态及已有回执。"
    elif "index" in raw or "索引" in raw:
        message = "索引处理异常，请检查索引服务配置和运行状态。"
    elif error:
        message = "执行异常。请使用任务 ID 排查服务日志；此处不展示可能包含私人内容的原始错误。"
    elif status in ATTENTION:
        message = "任务需要处理；请核对原任务状态。"
    else:
        message = "暂未记录执行错误。"
    return {"http_status": code, "message": message}


def detail(db, actor, kind, task_id):
    t = table(kind)
    meta = db.execute(select(t).where(t.c.id == task_id)).mappings().first()
    if not meta:
        raise NotFound("任务不存在")
    row = db.get(MODELS[kind], task_id)
    result = describe(db, [dict(meta)])[0]
    result["diagnosis"] = diagnosis(getattr(row, "error", None), row.status)
    result["has_upstream_receipt"] = bool(
        getattr(row, "external_job_id", None) or getattr(row, "remote_id", None)
    )
    if kind in {"upload", "attachment"}:
        result["transfer"] = {"uploaded_bytes": row.uploaded_bytes, "size_bytes": row.size_bytes}
    if kind in {"index", "transcription"}:
        result["progress"] = {"completed": row.completed, "total": row.total}
    if kind == "generation":
        result["cost"] = {"estimated": row.estimated_cost, "actual": row.actual_cost}
    # A platform administrator does not acquire project access by opening this page.
    pid = uuid.UUID(result["project_id"])
    member = db.scalar(
        select(Membership.id)
        .join(Project)
        .where(
            Membership.project_id == pid,
            Membership.user_id == actor.id,
            Project.deleted_at.is_(None),
        )
    )
    route = {
        "generation": "tasks",
        "production": "workbench?view=chat",
        "upload": "materials",
        "attachment": "materials",
        "index": "materials",
        "processing": "materials",
        "transcription": "transcriptions",
        "archive": "works",
    }[kind]
    result["project_url"] = f"/projects/{pid}/{route}" if member else None
    result["work_name"] = None
    result["summary"] = None
    if member and kind in {"production", "archive"}:
        work = db.get(CreativeWork, row.work_id) if row.work_id else None
        if work and work.project_id == pid:
            result["work_name"] = work.title
        if kind == "production":
            result["summary"] = row.goal[:300]
    return result
