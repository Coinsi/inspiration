"""Validated graph documents and immutable execution inputs; no asset copies."""

import copy
import hashlib
import json
import uuid

from sqlalchemy import select

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.canvas import Canvas, CanvasRevision, CanvasRun
from app.models.generation import GenerationJob
from app.modules.canvas.schemas import Document
from app.modules.generation import jobs


def get_canvas(db, project_id, canvas_id, lock=False):
    q = select(Canvas).where(Canvas.id == canvas_id, Canvas.project_id == project_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    c = db.scalar(q)
    if not c:
        raise NotFound("画布不存在")
    return c


def out(c):
    return {
        "id": c.id,
        "name": c.name,
        "revision": c.revision,
        "document": c.document,
        "updated_at": c.updated_at,
    }


def graph_order(document):
    nodes = {n["id"]: n for n in document["nodes"]}
    if len(nodes) != len(document["nodes"]):
        raise CapabilityUnsupported("节点编号重复")
    seen = set()
    connections = set()
    degree = {k: 0 for k in nodes}
    children = {k: [] for k in nodes}
    for n in nodes.values():
        parent = n.get("parentId")
        if parent and (
            parent not in nodes
            or nodes[parent]["data"]["kind"] != "group"
            or parent == n["id"]
            or nodes[parent].get("parentId")
        ):
            raise CapabilityUnsupported("分组无效；暂不支持嵌套分组")
    for e in document["edges"]:
        a, b = e["source"], e["target"]
        key = (a, b, e["targetHandle"])
        if e["id"] in seen or key in connections:
            raise CapabilityUnsupported("连线重复")
        seen.add(e["id"])
        connections.add(key)
        if a not in nodes or b not in nodes or a == b:
            raise CapabilityUnsupported("连线端点无效")
        source = nodes[a]["data"]
        target = nodes[b]["data"]
        if source["kind"] == "group" or target["kind"] != "generate":
            raise CapabilityUnsupported("输入只能连接到生成节点")
        expected = "prompt" if e["sourceHandle"] == "text" else "reference"
        if e["targetHandle"] != expected:
            raise CapabilityUnsupported("文字和图片端口类型不匹配")
        if e["sourceHandle"] == "image" and (
            source["kind"] == "text"
            or (source["kind"] == "generate" and source["generate"]["request_type"] != "image")
        ):
            raise CapabilityUnsupported("此节点不输出参考图片")
        degree[b] += 1
        children[a].append(b)
    ready = [k for k, v in degree.items() if v == 0]
    result = []
    while ready:
        k = ready.pop(0)
        result.append(k)
        for child in children[k]:
            degree[child] -= 1
            if degree[child] == 0:
                ready.append(child)
    if len(result) != len(nodes):
        raise CapabilityUnsupported("存在循环依赖，请移除形成回路的连线")
    return result


def resolve(db, project_id, node, strict=False):
    d = node["data"]
    kind = d["kind"]
    result = {"text": d["text"], "blob_hash": None, "generation_id": None}
    if kind in ("text", "group"):
        return result
    target_id = d.get("target_id")
    if not target_id:
        if strict:
            raise CapabilityUnsupported(f"节点「{d['label']}」尚未选择角色或镜头")
        return result
    target_type = kind if kind in ("shot", "asset") else d["target_type"]
    target = jobs.validate_target(db, project_id, target_type, uuid.UUID(target_id))
    result["text"] = "\n".join(
        filter(
            None,
            [
                d["text"],
                getattr(target, "name", None) or getattr(target, "title", None),
                getattr(target, "summary", None) or getattr(target, "description", None),
            ],
        )
    )
    result["version_id"] = str(target.current_version_id) if target.current_version_id else None
    generation_id = d.get("generation_id") or (
        str(target.selected_generation_id)
        if getattr(target, "selected_generation_id", None)
        else None
    )
    if generation_id:
        g = jobs.source(db, project_id, uuid.UUID(generation_id))
        if g.target_type != target_type or g.target_id != target.id:
            raise CapabilityUnsupported("候选结果不属于该节点引用的对象")
        if g.output_type == "image":
            result.update(blob_hash=g.output_blob_hash, generation_id=str(g.id))
    elif kind == "asset":
        result["blob_hash"] = target.representative_blob_hash
    return result


def validate(db, project_id, document, strict=False):
    data = Document.model_validate(document).model_dump(mode="json")
    if len(json.dumps(data, ensure_ascii=False)) > 4_000_000:
        raise CapabilityUnsupported("画布内容过大，请分成多张画布")
    graph_order(data)
    for n in data["nodes"]:
        aliases = set()
        for mention in n["data"].get("mentions", []):
            if mention["alias"] in aliases or not any(
                e["source"] == mention["node_id"] and e["target"] == n["id"] for e in data["edges"]
            ):
                raise CapabilityUnsupported("智能引用必须对应唯一名称和有效输入连线")
            aliases.add(mention["alias"])
        resolve(db, project_id, n, strict)
    return data


def save(db, ctx, canvas_id, data):
    c = get_canvas(db, ctx.project.id, canvas_id, True)
    if c.revision != data.revision:
        raise Conflict("画布已在其他窗口修改，请先重新载入；当前草稿仍保留")
    doc = validate(db, ctx.project.id, data.document.model_dump(mode="json"))
    if c.document == doc and c.name == data.name:
        return out(c)
    c.document = doc
    c.name = data.name
    c.revision += 1
    db.add(
        CanvasRevision(
            canvas_id=c.id,
            revision=c.revision,
            document={"name": c.name, **doc},
            created_by=ctx.user.id,
        )
    )
    audit.record(
        db,
        action="canvas.save",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="canvas",
        target_id=c.id,
        detail={"revision": c.revision},
    )
    db.flush()
    return out(c)


def start_run(db, ctx, canvas_id, data):
    c = get_canvas(db, ctx.project.id, canvas_id, True)
    prior = db.scalar(
        select(CanvasRun).where(
            CanvasRun.canvas_id == c.id, CanvasRun.request_key == data.request_key
        )
    )
    if prior:
        if (
            prior.revision != data.revision
            or prior.snapshot.get("target_node_id") != data.target_node_id
            or prior.snapshot.get("reuse_unchanged", False) != data.reuse_unchanged
        ):
            raise Conflict("此运行请求已用于其他版本或节点，请重新发起")
        return run_out(prior)
    if c.revision != data.revision:
        raise Conflict("请保存并重新载入最新画布后运行")
    active = db.scalar(
        select(CanvasRun).where(
            CanvasRun.canvas_id == c.id, CanvasRun.status.in_(["running", "paused"])
        )
    )
    if active:
        raise Conflict("此画布已有未结束批次，请先继续或取消")
    document = c.document
    if data.target_node_id:
        # Only the target and its upstream dependencies execute. Group containers
        # are retained for document validity, without pulling in sibling nodes.
        nodes = {n["id"]: n for n in document["nodes"]}
        target = nodes.get(data.target_node_id)
        if not target or target["data"]["kind"] != "generate":
            raise CapabilityUnsupported("请选择当前画布中的生成节点")
        included = {data.target_node_id}
        pending = [data.target_node_id]
        while pending:
            current = pending.pop()
            for edge in document["edges"]:
                if edge["target"] == current and edge["source"] not in included:
                    included.add(edge["source"])
                    pending.append(edge["source"])
        included.update(
            nodes[key]["parentId"] for key in list(included) if nodes[key].get("parentId")
        )
        document = {
            **document,
            "nodes": [n for n in document["nodes"] if n["id"] in included],
            "edges": [
                e for e in document["edges"] if e["source"] in included and e["target"] in included
            ],
        }
    doc = validate(db, ctx.project.id, document, True)
    if not any(n["data"]["kind"] == "generate" for n in doc["nodes"]):
        raise CapabilityUnsupported("请先添加生成节点")
    frozen = {n["id"]: resolve(db, ctx.project.id, n, True) for n in doc["nodes"]}
    for e in doc["edges"]:
        source = next(n for n in doc["nodes"] if n["id"] == e["source"])
        if (
            e["sourceHandle"] == "image"
            and source["data"]["kind"] != "generate"
            and not frozen[e["source"]]["blob_hash"]
        ):
            raise CapabilityUnsupported("参考节点没有图片，请先在对象中选择图片结果")
    from app.modules.skill.service import selected_versions, compose_documents
    from app.modules.skill.schemas import SkillUse
    from app.modules.identity.preferences import read as read_preferences
    skill_snapshots = {}
    project_guidance = read_preferences(ctx.project).generation_guidance
    for node in doc["nodes"]:
        if node["data"]["kind"] == "generate":
            chosen = [SkillUse(**s) for s in node["data"]["generate"].get("skills", [])]
            skill_snapshots[node["id"]] = selected_versions(db, ctx.project.id, chosen)
            compose_documents(skill_snapshots[node["id"]], "")
    fingerprints, ancestors = {}, {}
    ordered = graph_order(doc)
    by_id = {n["id"]: n for n in doc["nodes"]}
    for key in ordered:
        parents = [e["source"] for e in doc["edges"] if e["target"] == key]
        ancestors[key] = set(parents).union(*(ancestors[p] for p in parents))
        node = next(n for n in doc["nodes"] if n["id"] == key)
        payload = {
            "data": node["data"],
            "resolved": frozen[key],
            "skills": skill_snapshots.get(key, []),
            "project_guidance": project_guidance,
            "inputs": [
                [e["sourceHandle"], e["targetHandle"], fingerprints[e["source"]]]
                for e in doc["edges"]
                if e["target"] == key
            ],
        }
        fingerprints[key] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    steps = {n["id"]: {"status": "waiting", "attempt": 0} for n in doc["nodes"]}
    if data.reuse_unchanged:
        previous_runs = list(
            db.scalars(
                select(CanvasRun)
                .where(CanvasRun.canvas_id == c.id)
                .order_by(CanvasRun.created_at.desc())
                .limit(20)
            )
        )
        for key in ordered:
            node = by_id[key]
            if node["data"]["kind"] != "generate" or key == data.target_node_id:
                continue
            for old in previous_runs:
                step = old.steps.get(key, {})
                if (
                    step.get("status") != "succeeded"
                    or old.snapshot.get("fingerprints", {}).get(key) != fingerprints[key]
                ):
                    continue
                # Prompt equality is insufficient after a stochastic upstream rerun.
                # A downstream cache hit must have consumed the exact outputs we reuse now.
                generated_ancestors = [
                    a for a in ancestors[key] if by_id[a]["data"]["kind"] == "generate"
                ]
                if any(
                    not steps[a].get("reused")
                    or steps[a].get("output", {}).get("generation_id")
                    != old.steps.get(a, {}).get("output", {}).get("generation_id")
                    for a in generated_ancestors
                ):
                    continue
                try:
                    result = jobs.source(
                        db, ctx.project.id, uuid.UUID(step["output"]["generation_id"])
                    )
                    if (
                        str(result.target_id) != node["data"]["target_id"]
                        or result.target_type != node["data"]["target_type"]
                    ):
                        continue
                except (KeyError, ValueError, NotFound):
                    continue
                steps[key] = {**copy.deepcopy(step), "reused": True, "reused_from": str(old.id)}
                break
    run = CanvasRun(
        project_id=ctx.project.id,
        canvas_id=c.id,
        revision=c.revision,
        request_key=data.request_key,
        created_by=ctx.user.id,
        status="running",
        snapshot={
            **doc,
            "resolved": frozen,
            "skill_snapshots": skill_snapshots,
            "project_guidance": project_guidance,
            "target_node_id": data.target_node_id,
            "fingerprints": fingerprints,
            "reuse_unchanged": data.reuse_unchanged,
        },
        steps=steps,
    )
    db.add(run)
    db.flush()
    audit.record(
        db,
        action="canvas.run",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="canvas",
        target_id=c.id,
        detail={"run_id": str(run.id), "revision": c.revision},
    )
    return run_out(run)


def run_out(run):
    return {
        "id": run.id,
        "canvas_id": run.canvas_id,
        "revision": run.revision,
        "status": run.status,
        "steps": run.steps,
        "target_node_id": run.snapshot.get("target_node_id"),
        "error": run.error,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def control(db, ctx, canvas_id, run_id, action):
    get_canvas(db, ctx.project.id, canvas_id, True)
    r = db.scalar(
        select(CanvasRun)
        .where(CanvasRun.id == run_id, CanvasRun.canvas_id == canvas_id)
        .with_for_update()
    )
    if not r:
        raise NotFound("批次不存在")
    allowed = {
        "pause": ("running",),
        "resume": ("paused",),
        "cancel": ("running", "paused", "failed"),
        "retry": ("failed",),
    }
    if r.status not in allowed[action]:
        raise Conflict("批次状态已改变，请刷新后操作")
    if action == "retry":
        other = db.scalar(
            select(CanvasRun.id).where(
                CanvasRun.canvas_id == canvas_id, CanvasRun.status.in_(["running", "paused"])
            )
        )
        if other:
            raise Conflict("已有活动批次，请先完成或取消")
        steps = copy.deepcopy(r.steps)
        for step in steps.values():
            if step["status"] == "failed":
                if step.get("job_id"):
                    step["previous_jobs"] = [*step.get("previous_jobs", []), step["job_id"]]
                step.update(status="waiting", job_id=None, error=None)
        r.steps = steps
        r.error = None
    if action == "cancel":
        for step in r.steps.values():
            if step.get("job_id"):
                job = db.get(GenerationJob, uuid.UUID(step["job_id"]))
                if job and job.status in jobs.ACTIVE:
                    jobs.cancel(db, ctx, job.id)
    r.status = {"pause": "paused", "resume": "running", "cancel": "canceled", "retry": "running"}[
        action
    ]
    audit.record(
        db,
        action=f"canvas.{action}",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="canvas_run",
        target_id=r.id,
    )
    db.flush()
    return run_out(r)
