"""Small idempotent work setup, available independently of a decision engine."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import Conflict, Forbidden
from app.models.harness import ProductionRun, ProductionTask
from app.models.work import CreativeWork
from app.modules.tools import registry
from app.modules.tools.capabilities import ToolPolicy, register_contract


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    request_key: uuid.UUID


def prepare(db, ctx, context, args):
    from app.modules.conversation.works import get
    from app.modules.engineering.documents import initialize
    from app.modules.harness.execution_guard import authorize
    from app.modules.harness.service import event

    if not args.title.strip():
        raise Conflict("作品名称不能为空")
    grant = context.input_context.get("execution", {})
    run = None
    if grant.get("run_id"):
        authorize(db, ctx, context)
        run = db.get(ProductionRun, uuid.UUID(grant["run_id"]))
        task = db.get(ProductionTask, uuid.UUID(grant["task_id"]))
        if task.state.get("plan_grant") not in run.context.get("plan_grants", []):
            raise Forbidden("请先确认制作方案，再建立作品工程")
    existing = run.work_id if run else context.work_id
    if existing:
        work = get(db, ctx.project.id, uuid.UUID(str(existing)))
        created = False
    else:
        key = str(run.id) if run else str(args.request_key)
        identity = uuid.uuid5(ctx.project.id, f"production-work:{ctx.user.id}:{key}")
        work = db.get(CreativeWork, identity)
        created = work is None
        if work is None:
            work = CreativeWork(
                id=identity,
                project_id=ctx.project.id,
                title=args.title.strip(),
                created_by=ctx.user.id,
            )
            db.add(work)
            db.flush()
            initialize(db, ctx, work.id)
        if run:
            run.work_id = work.id
            event(db, run, "work.prepared", {"work_id": str(work.id), "created": created})
    return {
        "work_id": str(work.id),
        "title": work.title,
        "created": created,
        "engineering_revision": work.engineering_revision,
        "adopted": False,
        "note": "工程入口已建立；内容通过候选制作，采用仍由用户决定。",
    }


def register_tools():
    registry.register(
        registry.Tool(
            "work.prepare",
            "为已确认的组合制作准备最小作品工程；已有作品会复用，无需先填写工程表单。",
            Prepare,
            group="engineering",
        ),
        prepare,
    )
    register_contract(
        "work.prepare",
        ToolPolicy(
            effect="candidate",
            permissions=("narrative.edit", "engineering.edit"),
            scope="project",
            idempotency="request_key",
            cancellation="not_applicable",
            recovery="same_request",
            output_kind="work_reference",
        ),
    )
