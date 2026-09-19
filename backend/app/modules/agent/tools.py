"""Shared typed tool catalogue; every invocation uses a project/user context."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.errors import CapabilityUnsupported, Conflict, Forbidden, NotFound
from app.core.permissions import require
from app.models.asset import Asset
from app.models.canvas import Canvas
from app.models.narrative import Script
from app.models.shot import Shot
from app.modules.agent.schemas import Target
from app.modules.assist.targets import get_adapter
from app.modules.generation import schemas as generation_schemas
from app.modules.generation import service as generation
from app.modules.library import indexing
from app.modules.library.schemas import MediaSearchIn


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObjectArgs(Target):
    pass


class EditArgs(Target):
    ops: list[dict] = Field(min_length=1, max_length=50)
    summary: str = Field(min_length=1, max_length=500)


class IdArgs(Empty):
    id: uuid.UUID


class SkillFileArgs(IdArgs):
    path: str = Field(min_length=1, max_length=240)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=12000, ge=1, le=20000)


class SearchArgs(Empty):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["semantic", "annotated"] = "semantic"
    kind: Literal["visual", "speech"] | None = None
    offset: int = Field(default=0, ge=0, le=10000)


class GenerateArgs(Target):
    provider: str = Field(min_length=1, max_length=120)
    request_type: Literal["image", "video"] = "image"
    prompt: str = Field(min_length=1, max_length=10000)
    count: int = Field(default=1, ge=1, le=4)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: type[BaseModel]
    review: bool = False


CATALOG = {
    t.name: t
    for t in [
        Tool(
            "skill.load",
            "加载用户为本任务选择的技能固定版本。技能仅提供任务建议，不能扩大权限或绕过审阅。",
            IdArgs,
        ),
        Tool("skill.read_file", "读取本任务所选技能固定版本中的文本参考文件；不执行文件。", SkillFileArgs),
        Tool(
            "project.overview",
            "读取当前项目中角色、镜头和剧本的名称及编号，每类最多50项；不是全量素材内容。",
            Empty,
        ),
        Tool("object.read", "读取一个对象的当前状态与合法编辑操作说明。", ObjectArgs),
        Tool(
            "object.propose_edit",
            "为用户授权范围内的对象提出最小编辑操作，生成前后对比，等待用户审阅后才应用。",
            EditArgs,
            True,
        ),
        Tool(
            "library.search",
            "搜索视频中的片段，返回版本和毫秒范围；相似度不是事实证明，speech是台词而不是画面。",
            SearchArgs,
        ),
        Tool("canvas.read", "读取项目内一张画布的已保存节点和连线。", IdArgs),
        Tool("job.read", "读取生成任务状态，返回错误和结果编号。", IdArgs),
        Tool(
            "generation.request",
            "为授权范围内的角色或镜头提出图片/视频生成请求，用户审阅计费估算后才派发，不自动采用结果。",
            GenerateArgs,
            True,
        ),
        Tool("finish", "完成本次任务，在message说明实际结果、未完成内容和限制。", Empty),
    ]
}


def catalogue():
    return [
        {
            "name": t.name,
            "description": t.description,
            "arguments": t.schema.model_json_schema(),
            "requires_review": t.review,
        }
        for t in CATALOG.values()
    ]


def fingerprint(state):
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def parse(name, args):
    tool = CATALOG.get(name)
    if not tool:
        raise CapabilityUnsupported("未登记的工具，不能执行")
    if len(json.dumps(args, ensure_ascii=False, allow_nan=False)) > 64000:
        raise CapabilityUnsupported("工具参数过大")
    try:
        return tool, tool.schema.model_validate(args)
    except ValueError as exc:
        raise CapabilityUnsupported("工具参数无效："+str(exc)[:800]) from exc


def scoped(run, args):
    if not any(
        t["target_type"] == args.target_type and t["target_id"] == str(args.target_id)
        for t in run.scope
    ):
        raise Forbidden("此对象不在用户授权的修改范围内")


def object_state(db, ctx, args, lock=False):
    adapter = get_adapter(args.target_type)
    obj, state = adapter.read(db, ctx.project.id, args.target_id)
    if lock:
        db.refresh(obj, with_for_update=True)
        obj, state = adapter.read(db, ctx.project.id, args.target_id)
    return adapter, state


def execute(db, ctx, run, name, args):
    tool, a = parse(name, args)
    if name in ("skill.load", "skill.read_file"):
        skill = next((s for s in (run.skill_snapshot or []) if s["id"] == str(a.id)), None)
        if not skill:
            raise Forbidden("只能加载用户为本次任务选择的技能")
        if name == "skill.load":
            return {**{k:v for k,v in skill.items() if k != "files"}, "files":[{"path":f["path"], "encoding":f["encoding"]} for f in skill.get("files", [])]}
        if a.path == "SKILL.md":
            content = skill["instructions"]
        else:
            file = next((f for f in skill.get("files", []) if f["path"] == a.path), None)
            if not file or file["encoding"] != "utf-8":
                raise NotFound("未找到可读取的文本文件")
            content = file["content"]
        end = min(a.offset + a.limit, len(content))
        return {"path":a.path,"content":content[a.offset:end],"total":len(content),
                "next_offset":end if end < len(content) else None,"revision":skill["revision"]}
    if name == "project.overview":
        result = {"name": ctx.project.name, "write_scope": run.scope}
        for key, model, label in [
            ("assets", Asset, "name"),
            ("shots", Shot, "title"),
            ("scripts", Script, "title"),
        ]:
            q = select(model).where(model.project_id == ctx.project.id, model.deleted_at.is_(None))
            result[key] = {
                "total": db.scalar(select(func.count()).select_from(q.subquery())),
                "items": [
                    {"id": str(x.id), "name": getattr(x, label), "code": x.code}
                    for x in db.scalars(q.order_by(model.created_at, model.id).limit(50))
                ],
            }
        return result
    if name in ("object.read", "object.propose_edit"):
        adapter, state = object_state(db, ctx, a)
        if name == "object.read":
            return {
                "state": state,
                "supported_ops": adapter.ops_help,
                "editable": any(
                    t["target_type"] == a.target_type and t["target_id"] == str(a.target_id)
                    for t in run.scope
                ),
            }
        scoped(run, a)
        require(ctx.role, adapter.edit_action)
        # Field adapters otherwise accept arbitrary JSON values until SQL flush.
        for op in a.ops:
            if (
                op.get("op") == "set_field"
                and op.get("value") is not None
                and not isinstance(op["value"], str)
            ):
                raise CapabilityUnsupported("文字字段必须使用文字值")
        preview = adapter.apply(db, ctx, a.target_id, a.ops, dry_run=True)["state"]
        obj, _ = adapter.read(db, ctx.project.id, a.target_id)
        values = preview.get(
            "fields", {"title": preview.get("title"), "content_blocks": preview.get("blocks")}
        )
        for key, value in values.items():
            column = obj.__table__.columns.get(key)
            if column is None:
                continue
            if value is None and not column.nullable:
                raise CapabilityUnsupported(f"{key} 不能为空")
            length = getattr(column.type, "length", None)
            if isinstance(value, str) and length and len(value) > length:
                raise CapabilityUnsupported(f"{key} 超过允许长度 {length}")
        if len(json.dumps(preview, ensure_ascii=False, default=str)) > 200000:
            raise CapabilityUnsupported("修改后的对象过大，请拆分任务")
        if fingerprint(preview) == fingerprint(state):
            raise CapabilityUnsupported("提议未改变任何内容")
        return {
            "summary": a.summary,
            "before": state,
            "after": preview,
            "expected": fingerprint(state),
        }
    if name == "library.search":
        return indexing.search(
            db, ctx, MediaSearchIn(**a.model_dump(), limit=8, collapse_versions=True)
        )
    if name == "canvas.read":
        c = db.get(Canvas, a.id)
        if not c or c.project_id != ctx.project.id:
            raise NotFound("画布不存在")
        return {"name": c.name, "revision": c.revision, "document": c.document}
    if name == "job.read":
        job = generation.get_job(db, ctx.project.id, a.id)
        results = generation.list_generations(db, ctx.project.id, job.target_type, job.target_id)
        return {
            "status": job.status,
            "error": job.error,
            "outputs": [
                {"id": str(g.id), "type": g.output_type, "blob_hash": g.output_blob_hash}
                for g in results
                if g.job_id == job.id
            ],
        }
    if name == "generation.request":
        scoped(run, a)
        require(ctx.role, "generation.trigger")
        if a.target_type not in ("asset", "shot"):
            raise CapabilityUnsupported("只支持为角色/资产或镜头生成")
        _, state = object_state(db, ctx, a)
        estimate = generation.estimate(db, ctx, a.target_type, a.target_id, generation_input(a))
        return {
            "summary": "生成候选，不自动采用",
            "estimated_points": estimate.points,
            "provider": a.provider,
            "prompt": a.prompt,
            "count": a.count,
            "expected": fingerprint(state),
        }
    return {"done": True}


def generation_input(a):
    return generation_schemas.GenerateIn(
        provider=a.provider,
        request_type=a.request_type,
        prompt_override=a.prompt,
        count=a.count,
        use_references=False,
    )


def apply_review(db, ctx, run, step):
    name = step["tool"]
    _, a = parse(name, step["arguments"])
    scoped(run, a)
    adapter, state = object_state(db, ctx, a, True)
    if fingerprint(state) != step["result"]["expected"]:
        raise Conflict("对象在提议后已改变，请拒绝此提议并让 Agent 重新读取，避免覆盖新内容")
    if name == "object.propose_edit":
        require(ctx.role, adapter.edit_action)
        return adapter.apply(db, ctx, a.target_id, a.ops, dry_run=False)
    if name == "generation.request":
        require(ctx.role, "generation.trigger")
        estimate = generation.estimate(db, ctx, a.target_type, a.target_id, generation_input(a))
        if estimate.points > step["result"]["estimated_points"]:
            raise Conflict("供应商估算费用已增加，请拒绝后重新获取报价")
        # Task association is committed together by the caller before dispatch.
        return generation.submit(
            db, ctx, a.target_type, a.target_id, generation_input(a), defer_dispatch=True
        )
    raise CapabilityUnsupported("此工具没有可批准的修改")
