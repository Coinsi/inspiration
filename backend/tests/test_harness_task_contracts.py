"""Cross-cutting task grants, read prerequisites, plan isolation and lazy work setup."""

# ruff: noqa: F811
import uuid

import pytest

from app.core.errors import Forbidden
from app.models.harness import ProductionRun, ProductionTask
from app.modules.harness import plan_contracts, runtime, schemas, service
from tests.test_harness_context import context
from tests.test_harness_runtime import (  # noqa: F401
    command,
    plan,
    registered_fixture_kernel,
    seeded,
)
from tests.test_media_workflow import studio  # noqa: F401


def test_child_scope_and_tool_grants_only_narrow(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        scope = [{"target_type": "shot", "target_id": ids["shot"]}]
        run.context = {**run.context, "scope": scope}
        parent = service.tasks(db, run)[0]
        parent.specification = {
            **parent.specification,
            "scope": scope,
            "allowed_tools": ["project.overview"],
        }
        frozen = plan_contracts.validate_plan(
            db, context(db, ids), run, plan(("child", {})), parent
        )
        assert frozen["child"]["grants"] == {"scope": scope, "allowed_tools": ["project.overview"]}
        with pytest.raises(Forbidden):
            plan_contracts.validate_plan(
                db,
                context(db, ids),
                run,
                plan(("child", {"allowed_tools": ["media.generate"]})),
                parent,
            )
        with pytest.raises(Forbidden):
            plan_contracts.validate_plan(
                db,
                context(db, ids),
                run,
                plan(
                    ("child", {"scope": [{"target_type": "shot", "target_id": str(uuid.uuid4())}]})
                ),
                parent,
            )
        service.add_plan(db, run, plan(("child", {"scope": [], "allowed_tools": []})), parent)
        child = next(t for t in service.tasks(db, run) if t.parent_id == parent.id)
        assert child.specification["scope"] == child.specification["allowed_tools"] == []


def test_discovery_cannot_expose_a_forbidden_worker_tool(studio):
    sessions, _, rid = seeded(
        studio, specs=plan(("limited", {"allowed_tools": ["project.overview"]}))
    )
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
        runtime.prepare(db, ticket)
        runtime.apply_action(
            db, ticket, schemas.ToolAction(kind="tool", tool="capabilities.discover", arguments={})
        )
        db.commit()
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert task.state["loaded_tools"] == ["project.overview"]
        next_ticket = service.claim(db, rid)
        with pytest.raises(Forbidden):
            runtime.apply_action(
                db,
                next_ticket,
                schemas.ToolAction(kind="tool", tool="media.generate", arguments={}),
            )


def test_parallel_plan_proposals_keep_distinct_approval_targets(studio):
    sessions, ids, rid = seeded(studio, specs=plan(("one", {}), ("two", {})))
    with sessions() as db:
        a = service.claim(db, rid)
        b = service.claim(db, rid)
        db.commit()
        runtime.apply_action(db, a, schemas.PlanAction(kind="plan", plan=plan(("first-child", {}))))
        runtime.apply_action(
            db, b, schemas.PlanAction(kind="plan", plan=plan(("second-child", {})))
        )
        run = db.get(ProductionRun, rid)
        assert run.plan["tasks"][0]["key"] == "first-child"
        command(db, ids, rid, "approve_plan")
        assert run.status == "needs_plan_approval"
        assert run.plan["tasks"][0]["key"] == "second-child"
        first_child = next(
            t for t in service.tasks(db, run) if t.specification["key"] == "first-child"
        )
        assert str(first_child.parent_id) == a["task_id"]
        command(db, ids, rid, "approve_plan")
        second_child = next(
            t for t in service.tasks(db, run) if t.specification["key"] == "second-child"
        )
        assert str(second_child.parent_id) == b["task_id"]


def test_work_setup_is_lazy_idempotent_and_requires_plan_grant(studio):
    from app.modules.harness.work_tools import Prepare, prepare

    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        ticket = service.claim(db, rid)
        run = db.get(ProductionRun, rid)
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        args = Prepare(title="试验作品", request_key=uuid.uuid4())
        with pytest.raises(Forbidden):
            prepare(db, context(db, ids), runtime.tool_context(run, ticket, task), args)
        grant = str(uuid.uuid4())
        run.context = {**run.context, "plan_grants": [grant]}
        task.state = {**task.state, "plan_grant": grant}
        db.flush()
        first = prepare(db, context(db, ids), runtime.tool_context(run, ticket, task), args)
        db.commit()
        again = prepare(
            db,
            context(db, ids),
            runtime.tool_context(run, ticket, task),
            Prepare(title="不要重命名", request_key=uuid.uuid4()),
        )
        assert first["work_id"] == again["work_id"] == str(run.work_id)
        assert again["title"] == "试验作品" and not again["created"]


def test_document_read_prerequisite_cannot_be_dropped_by_later_tool(studio):
    from app.modules.harness.read_tracking import merge, track
    from app.modules.tools.contracts import ToolContext

    ref = {"id": str(uuid.uuid4()), "revision": 7}
    records = track([], {"view": "document"}, ref)
    assert merge(ToolContext(input_context={"read_preconditions": records})) == [
        {"kind": "document", **ref}
    ]
