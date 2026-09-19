import copy
import uuid

from sqlalchemy import select

from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.permissions import require
from app.models.agent import AgentRun
from app.models.identity import Membership, Project, User
from app.modules.agent import tools
from app.modules.assist.targets import get_adapter
from app.modules.generation import jobs

ACTIVE = ("queued", "thinking", "running", "waiting_review", "waiting_job", "paused")


def get_run(db, ctx, run_id, lock=False):
    q = select(AgentRun).where(AgentRun.id == run_id, AgentRun.project_id == ctx.project.id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    r = db.scalar(q)
    if not r:
        raise NotFound("Agent 任务不存在")
    return r


def owner(ctx, run):
    require(ctx.role, "agent.run")
    if ctx.user.id != run.created_by and ctx.role.value not in ("admin", "director"):
        raise Forbidden("只能操作自己发起的 Agent 任务")


def context(db, run):
    project = db.get(Project, run.project_id)
    user = db.get(User, run.created_by)
    member = db.scalar(
        select(Membership).where(
            Membership.project_id == run.project_id, Membership.user_id == run.created_by
        )
    )
    if not project or project.deleted_at or not user or not user.is_active or not member:
        raise Forbidden("项目或发起者已不可用")
    ctx = ProjectContext(project, member, user)
    require(ctx.role, "agent.run")
    return ctx


def out(r, detail=True):
    return {
        "id": r.id,
        "goal": r.goal,
        "engine": r.engine,
        "status": r.status,
        "scope": r.scope,
        "skills": [
            {"id": s["id"], "name": s["name"], "revision": s["revision"]}
            for s in (r.skill_snapshot or [])
        ],
        "max_turns": r.max_turns,
        "turns": r.turns,
        "result": r.result,
        "error": r.error,
        "created_by": r.created_by,
        "created_at": r.created_at,
        **({"steps": r.steps} if detail else {}),
    }


def create(db, ctx, data):
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    prior = db.scalar(
        select(AgentRun).where(
            AgentRun.project_id == ctx.project.id,
            AgentRun.created_by == ctx.user.id,
            AgentRun.request_key == data.request_key,
        )
    )
    if prior:
        return out(prior)
    for target in data.scope:
        adapter = get_adapter(target.target_type)
        adapter.read(db, ctx.project.id, target.target_id)
        require(ctx.role, adapter.edit_action)
    from app.modules.skill.service import snapshots

    if data.skill_versions:
        from app.modules.skill.service import selected_versions
        if {s.id for s in data.skill_versions} != set(data.skills):
            raise Conflict("所选技能与版本不一致，请重新选择")
        selected_skills = selected_versions(db, ctx.project.id, data.skill_versions)
    else:
        selected_skills = snapshots(db, ctx, data.skills)
    r = AgentRun(
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        request_key=data.request_key,
        goal=data.goal,
        engine=data.engine,
        scope=[t.model_dump(mode="json") for t in data.scope],
        skill_snapshot=selected_skills,
        max_turns=data.max_turns,
        turns=0,
        steps=[],
        status="queued",
    )
    db.add(r)
    db.flush()
    audit.record(
        db,
        action="agent.start",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="agent_run",
        target_id=r.id,
        detail={"max_turns": r.max_turns, "scope": r.scope},
    )
    return out(r)


def review(db, ctx, run_id, step_id, data):
    r = get_run(db, ctx, run_id, True)
    owner(ctx, r)
    steps = copy.deepcopy(r.steps)
    step = next((s for s in steps if s["id"] == step_id), None)
    if not step:
        raise NotFound("步骤不存在")
    if step.get("decision"):
        if step["decision"] == data.action:
            return out(r)
        raise Conflict("此提议已作出决定")
    if r.status != "waiting_review" or step["status"] != "review":
        raise Conflict("此步骤不在等待审阅")
    job = None
    if data.action == "approve":
        result = tools.apply_review(db, ctx, r, step)
        if step["tool"] == "generation.request":
            job = result
            job.input_snapshot = {
                **job.input_snapshot,
                "agent_origin": {"run_id": str(r.id), "step_id": step["id"]},
            }
            step.update(status="waiting", job_id=str(job.id))
            r.status = "waiting_job"
        else:
            step.update(status="succeeded", applied=result)
            r.status = "queued"
    else:
        step["status"] = "rejected"
        r.status = "queued"
    step.update(decision=data.action, decision_by=str(ctx.user.id), note=data.note)
    if r.engine == "external" and r.status == "queued":
        r.status = "succeeded" if data.action == "approve" else "canceled"
        r.result = "外部工具提议已处理"
    r.steps = steps
    r.error = None
    audit.record(
        db,
        action=f"agent.{data.action}",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="agent_run",
        target_id=r.id,
        detail={"step": step_id, "tool": step["tool"]},
    )
    db.flush()
    if job:
        jobs.dispatch(db, job)
    return out(r)


def control(db, ctx, run_id, data):
    r = get_run(db, ctx, run_id, True)
    owner(ctx, r)
    if data.action == "pause":
        if r.status not in ("queued", "thinking", "running", "waiting_job"):
            raise Conflict("当前任务不能暂停")
        r.status = "paused"
        r.claim_id = None
    elif data.action == "cancel":
        if r.status not in ACTIVE:
            raise Conflict("任务已经结束")
        for step in r.steps:
            if step.get("job_id"):
                from app.models.generation import GenerationJob

                job = db.get(GenerationJob, uuid.UUID(step["job_id"]))
                if job and job.status in jobs.ACTIVE:
                    jobs.cancel(db, ctx, job.id)
        r.status = "canceled"
        r.claim_id = None
    else:
        if r.engine == "external":
            raise Conflict("外部工具任务请从原客户端重新发起")
        if r.status not in ("paused", "failed"):
            raise Conflict("当前任务不能继续")
        if r.turns >= r.max_turns:
            if data.additional_turns < 1 or r.max_turns + data.additional_turns > 96:
                raise Conflict("需要追加执行步数，单个任务最多96轮")
            r.max_turns += data.additional_turns
        last = r.steps[-1] if r.steps else {}
        r.status = (
            "waiting_job"
            if last.get("status") == "waiting"
            else "running"
            if last.get("status") == "pending"
            else "queued"
        )
        r.error = None
    audit.record(
        db,
        action=f"agent.{data.action}",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="agent_run",
        target_id=r.id,
    )
    db.flush()
    return out(r)


def invoke_external(db, ctx, data):
    from types import SimpleNamespace

    from app.core.errors import CapabilityUnsupported

    spec, args = tools.parse(data.tool, data.arguments)
    if data.tool in ("finish", "skill.load", "skill.read_file"):
        raise CapabilityUnsupported("此工具仅用于在线任务内部，请通过技能目录读取技能")
    if not spec.review:
        result = tools.execute(
            db, ctx, SimpleNamespace(scope=[], skill_snapshot=[]), data.tool, data.arguments
        )
        audit.record(
            db,
            action="tool.external_read",
            user_id=ctx.user.id,
            project_id=ctx.project.id,
            detail={"tool": data.tool},
        )
        return {"kind": "result", "result": result}
    require(ctx.role, "agent.run")
    if not data.request_key:
        raise CapabilityUnsupported("修改提议必须提供UUID请求编号，重试请使用同一编号")
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    old = db.scalar(
        select(AgentRun).where(
            AgentRun.project_id == ctx.project.id,
            AgentRun.created_by == ctx.user.id,
            AgentRun.request_key == data.request_key,
        )
    )
    if old:
        if (
            old.engine != "external"
            or old.steps[0]["tool"] != data.tool
            or old.steps[0]["arguments"] != data.arguments
        ):
            raise Conflict("此请求编号已用于其他内容")
        return {"kind": "review", "run": out(old)}
    scope = [{"target_type": args.target_type, "target_id": str(args.target_id)}]
    r = AgentRun(
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        request_key=data.request_key,
        goal="外部工具：" + spec.description,
        engine="external",
        scope=scope,
        skill_snapshot=[],
        max_turns=0,
        turns=0,
        steps=[],
        status="waiting_review",
    )
    result = tools.execute(db, ctx, r, data.tool, data.arguments)
    r.steps = [
        {
            "id": uuid.uuid4().hex,
            "tool": data.tool,
            "arguments": data.arguments,
            "message": "外部 AI 提出的操作，请核对后决定是否应用。",
            "status": "review",
            "result": result,
        }
    ]
    db.add(r)
    db.flush()
    audit.record(
        db,
        action="tool.external_proposal",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="agent_run",
        target_id=r.id,
        detail={"tool": data.tool},
    )
    return {"kind": "review", "run": out(r)}
