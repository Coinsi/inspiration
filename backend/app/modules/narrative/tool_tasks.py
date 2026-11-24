"""Durable creative operations. Model execution and candidate adoption are separate."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.core.database import SessionLocal, get_db
from app.core.deps import ProjectContext, get_project_context
from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.permissions import require
from app.models.identity import Project, User, Membership
from app.models.narrative import Chapter, Scene, CreativeToolTask
from app.modules.narrative import service, schemas

Operation = Literal[
    "script_decompose", "script_entities", "scene_breakdown", "chapter_decompose", "assist_message"
]
router = APIRouter(prefix="/projects/{project_id}/creative-tool-tasks", tags=["creative tools"])


class Submit(BaseModel):
    operation: Operation
    target_id: uuid.UUID
    request_key: uuid.UUID
    content: str = Field(default="", max_length=20000)
    skills: list[dict] = Field(default_factory=list, max_length=4)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(jsonable_encoder(value), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def source(db, ctx, operation, target_id, *, lock=False):
    """Validate project boundaries and obtain the business revision used by this tool."""
    if operation == "assist_message":
        from app.modules.assist.service import _get_chat
        from app.modules.assist.targets import get_adapter

        chat = _get_chat(db, ctx.project.id, target_id)
        obj, state = get_adapter(chat.target_type).read(db, ctx.project.id, chat.target_id)
        # Conversation history is also input: another message invalidates queued context.
        value = {"state": state, "messages": chat.messages}
        action = get_adapter(chat.target_type).edit_action
        obj = chat
    elif operation.startswith("script_"):
        obj = service._get_script(db, ctx.project.id, target_id)
        value = {"title": obj.title, "blocks": obj.content_blocks}
        action = "narrative.edit"
    elif operation == "chapter_decompose":
        obj = service._get_chapter(db, ctx.project.id, target_id)
        value = {"content": obj.content, "title": obj.title}
        action = "narrative.edit"
    else:
        obj = db.get(Scene, target_id)
        if not obj or obj.project_id != ctx.project.id or obj.deleted_at:
            raise NotFound("场景不存在")
        value = {"title": obj.title, "body": obj.body, "summary": obj.summary}
        action = "shot.edit"
    require(ctx.role, action)
    if lock:
        db.refresh(obj, with_for_update=True)
        return source(db, ctx, operation, target_id)
    return fingerprint(value)


def latest(db, project_id, operation, target_id):
    return db.scalar(
        select(CreativeToolTask)
        .where(
            CreativeToolTask.project_id == project_id,
            CreativeToolTask.operation == operation,
            CreativeToolTask.target_id == target_id,
        )
        .order_by(CreativeToolTask.created_at.desc())
        .limit(1)
    )


@router.get("")
def current(
    operation: Operation,
    target_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db=Depends(get_db, scope="function"),
):
    source(db, ctx, operation, target_id)
    return latest(db, ctx.project.id, operation, target_id)


@router.get("/recent")
def recent(
    ctx: ProjectContext = Depends(get_project_context), db=Depends(get_db, scope="function")
):
    return list(
        db.scalars(
            select(CreativeToolTask)
            .where(
                CreativeToolTask.project_id == ctx.project.id,
                CreativeToolTask.created_by == ctx.user.id,
                CreativeToolTask.operation.in_(["scene_breakdown", "chapter_decompose"]),
            )
            .order_by(CreativeToolTask.created_at.desc())
            .limit(30)
        )
    )


@router.post("", status_code=202)
def submit(
    data: Submit,
    ctx: ProjectContext = Depends(get_project_context),
    db=Depends(get_db, scope="function"),
):
    revision = source(db, ctx, data.operation, data.target_id)
    # Short project lock makes first submissions and cross-tab retries atomic.
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    previous = db.scalar(
        select(CreativeToolTask).where(
            CreativeToolTask.project_id == ctx.project.id,
            CreativeToolTask.request_key == data.request_key,
        )
    )
    payload = {"content": data.content, "skills": data.skills}
    if data.operation == "assist_message":
        from app.modules.assist.schemas import PostMessageIn

        payload = PostMessageIn(content=data.content, skills=data.skills).model_dump(mode="json")
        if not data.content.strip():
            raise Conflict("请输入消息")
    if previous:
        if (
            previous.target_id != data.target_id
            or previous.operation != data.operation
            or previous.payload != payload
        ):
            raise Conflict("同一提交标识不能用于不同内容")
        return previous
    active = latest(db, ctx.project.id, data.operation, data.target_id)
    if (
        active
        and active.status == "failed"
        and active.source_revision == revision
        and data.operation in {"script_decompose", "chapter_decompose"}
        and (active.result or {}).get("output", {}).get("scenes")
    ):
        active.status, active.error = "queued", None
        return active
    incomplete_shots = (
        active
        and data.operation == "scene_breakdown"
        and any(
            (row.get("storyboard") or {}).get("breakdown_error")
            for row in (active.result or {}).get("output", [])
        )
    )
    if (
        active
        and not incomplete_shots
        and data.operation != "assist_message"
        and active.source_revision == revision
        and active.status in {"queued", "running", "succeeded", "applied"}
    ):
        return active
    if active and active.status in {"queued", "running"}:
        raise Conflict("该对象已有任务执行中，请等待完成后再提交")
    task = CreativeToolTask(
        project_id=ctx.project.id,
        target_id=data.target_id,
        operation=data.operation,
        request_key=data.request_key,
        created_by=ctx.user.id,
        payload=payload,
        source_revision=revision,
    )
    db.add(task)
    db.flush()
    return task


def execute(db, ctx, task):
    if source(db, ctx, task.operation, task.target_id) != task.source_revision:
        raise Conflict("内容已变化，请根据最新内容重新发起")
    if task.operation in {"script_decompose", "chapter_decompose"}:

        def checkpoint(result):
            task.result = {**(task.result or {}), "output": result.model_dump(mode="json")}
            db.commit()
            db.refresh(task, with_for_update=True)
            if task.status != "running":
                raise Conflict("场次任务状态已更新")

        previous = (task.result or {}).get("output")
        decompose = (
            service.decompose_script
            if task.operation == "script_decompose"
            else service.decompose_chapter
        )
        result = decompose(db, ctx.project.id, task.target_id, None, previous=previous, checkpoint=checkpoint)
    elif task.operation == "script_entities":
        result = service.extract_entities(
            db, ctx.project.id, schemas.ExtractEntitiesIn(script_id=task.target_id), None
        )
    elif task.operation == "scene_breakdown":
        from app.modules.shot.service import breakdown_scene
        from app.modules.shot.schemas import ShotOut

        if source(db, ctx, task.operation, task.target_id, lock=True) != task.source_revision:
            raise Conflict("场景已修改，请重新生成分镜")

        def save_shot():
            db.commit()
            db.refresh(task, with_for_update=True)
            if (
                task.status != "running"
                or source(db, ctx, task.operation, task.target_id, lock=True)
                != task.source_revision
            ):
                raise Conflict("场景已变化，已保留完成的镜头")

        result = [ShotOut.model_validate(item) for item in breakdown_scene(db, ctx, task.target_id, checkpoint=save_shot)]
    else:
        from app.modules.assist import service as assist, schemas as assist_schemas

        result = assist.post_message(
            db, ctx, task.target_id, assist_schemas.PostMessageIn.model_validate(task.payload)
        )
    receipt = {"output": jsonable_encoder(result)}
    if task.operation == "chapter_decompose":
        receipt["novel_id"] = str(db.get(Chapter, task.target_id).novel_id)
    return receipt


@router.post("/{task_id}/retry-scenes", status_code=202)
def retry_scenes(
    task_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db=Depends(get_db, scope="function"),
):
    task = db.scalar(
        select(CreativeToolTask)
        .where(CreativeToolTask.id == task_id, CreativeToolTask.project_id == ctx.project.id)
        .with_for_update()
    )
    if not task or task.operation not in {"script_decompose", "chapter_decompose"}:
        raise NotFound("找不到这次拆镜任务")
    if source(db, ctx, task.operation, task.target_id) != task.source_revision:
        raise Conflict("原文已修改，请从最新内容重新拆解")
    if task.status in {"queued", "running"}:
        return task
    scenes = (task.result or {}).get("output", {}).get("scenes", [])
    if not any(row.get("error") for row in scenes):
        return task
    task.status, task.error = "queued", None
    return task


class Adopt(BaseModel):
    selected: list[int] | None = None
    matches: dict[int, uuid.UUID | None] = Field(default_factory=dict)


@router.post("/{task_id}/apply")
def apply(
    task_id: uuid.UUID,
    data: Adopt,
    ctx: ProjectContext = Depends(get_project_context),
    db=Depends(get_db, scope="function"),
):
    task = db.scalar(
        select(CreativeToolTask)
        .where(CreativeToolTask.id == task_id, CreativeToolTask.project_id == ctx.project.id)
        .with_for_update()
    )
    if not task:
        raise NotFound("任务不存在")
    if task.status == "applied":
        return {"applied": True}
    if task.status != "succeeded" or task.operation not in {"script_decompose", "script_entities"}:
        raise Conflict("任务尚不可应用")
    if source(db, ctx, task.operation, task.target_id, lock=True) != task.source_revision:
        raise Conflict("剧本已修改，这份建议已过期，请重新生成")
    output = task.result["output"]
    if task.operation == "script_decompose":
        service.apply_scenes(
            db, ctx, task.target_id, schemas.ApplyScenesIn(scenes=output["scenes"])
        )
    else:
        require(ctx.role, "asset.edit")
        selected = data.selected if data.selected is not None else list(range(len(output)))
        if len(set(selected)) != len(selected) or any(i < 0 or i >= len(output) for i in selected):
            raise Conflict("选择的条目无效")
        entities = []
        for i in selected:
            item = dict(output[i])
            if i in data.matches:
                item.update(asset_id=data.matches[i], separate=data.matches[i] is None)
            entities.append(item)
        try:
            service.apply_entities(
                db, ctx, schemas.ApplyEntitiesIn(script_id=task.target_id, entities=entities)
            )
        except AppError as exc:
            if "index" in exc.detail:
                exc.detail["index"] = selected[exc.detail["index"]]
            raise
    if task.operation != "script_decompose" or not any(
        row.get("error") for row in output["scenes"]
    ):
        task.status = "applied"
    return {"applied": True}


def run_once():
    with SessionLocal() as db:
        task = db.scalar(
            select(CreativeToolTask)
            .where(
                (CreativeToolTask.status == "queued")
                | (
                    (CreativeToolTask.status == "running")
                    & (CreativeToolTask.updated_at < datetime.now(UTC) - timedelta(minutes=10))
                )
            )
            .order_by(CreativeToolTask.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not task:
            return False
        if task.status == "running":
            task.status, task.error = "failed", "任务被中断，未保存结果，请检查后重试"
            db.commit()
            return True
        task.status = "running"
        task_id = task.id
        db.commit()
        task = db.scalar(
            select(CreativeToolTask).where(CreativeToolTask.id == task_id).with_for_update()
        )
        try:
            user, project = db.get(User, task.created_by), db.get(Project, task.project_id)
            membership = db.scalar(
                select(Membership).where(
                    Membership.project_id == task.project_id, Membership.user_id == task.created_by
                )
            )
            if (
                not user
                or not user.is_active
                or not project
                or project.deleted_at
                or not membership
            ):
                raise Forbidden("账号或项目权限已变化")
            task.result = execute(db, ProjectContext(project, membership, user), task)
            task.status = "succeeded"
            db.commit()
        except Exception as exc:
            db.rollback()
            task = db.get(CreativeToolTask, task_id)
            task.status = "failed"
            task.error = exc.message if isinstance(exc, AppError) else "执行失败，请检查后重试"
            db.commit()
    return True
