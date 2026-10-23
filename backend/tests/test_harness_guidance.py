"""User input is ordered, scoped and leased; no provider calls or user data."""

# ruff: noqa: F811
import copy
import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import Conflict
from app.models.harness import ProductionAttempt, ProductionEvent, ProductionRun, ProductionTask
from app.modules.harness import guidance, kernel, runtime, service
from app.modules.harness.context import effective_facts
from tests.test_harness_context import context
from tests.test_harness_runtime import command, plan, seeded
from tests.test_media_workflow import studio  # noqa: F401


def received(db, ids, rid, text="开场雨太大了，只调整开场", **kw):
    result = guidance.receive(
        db, context(db, ids), rid, kw.pop("request_key", uuid.uuid4()), text, **kw
    )
    run = db.get(ProductionRun, rid)
    ticket = service.claim(db, rid)
    assert ticket and guidance.is_impact(db.get(ProductionTask, uuid.UUID(ticket["task_id"])))
    return run, ticket, result["guidance_id"]


def decision(gid, action="local", text="开场雨太大了", task_ids=(), **kw):
    return guidance.Decision(
        guidance_id=gid,
        action=action,
        message=kw.pop("message", "只重新调整开场，其他保持"),
        task_ids=task_ids,
        evidence=kw.pop("evidence", [{"task_id": str(t), "quote": text} for t in task_ids]),
        **kw,
    )


def test_pending_barrier_is_ordered_idempotent_and_preserves_existing_running_job(studio):
    sessions, ids, rid = seeded(studio, plan(("opening", {}), ("music", {})), max_parallel=1)
    with sessions() as db:
        original = service.claim(db, rid)
        original_task = db.get(ProductionTask, uuid.UUID(original["task_id"]))
        key = uuid.uuid4()
        run, impact, gid = received(db, ids, rid, request_key=key)
        assert original_task.status == "running"
        assert guidance.pending(run) == gid
        assert service.claim(db, rid) is None
        before = len(service.tasks(db, run))
        assert (
            guidance.receive(db, context(db, ids), rid, key, "开场雨太大了，只调整开场")[
                "guidance_id"
            ]
            == gid
        )
        assert len(service.tasks(db, run)) == before
        with pytest.raises(Conflict):
            guidance.receive(db, context(db, ids), rid, key, "改其他内容")
        # A decision already in flight is retained, but cannot begin writing.
        from app.modules.harness.schemas import ToolAction

        runtime.apply_action(
            db, original, ToolAction(kind="tool", tool="engineering.compose", arguments={})
        )
        assert original_task.status == "queued"
        assert original_task.state["pending_action"]["tool"] == "engineering.compose"
        assert guidance.resolve(
            db, impact, decision(gid, "discussion", message="这里可以先聊配乐。")
        )
        assert not guidance.pending(run)
        assert original_task.status == "queued"


def test_local_scope_fences_late_work_but_preserves_sibling_and_user_pause(studio):
    sessions, ids, rid = seeded(studio, plan(("opening", {}), ("music", {})))
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        old = service.claim(db, rid)
        target = uuid.UUID(old["task_id"])
        sibling = next(t for t in service.tasks(db, run) if t.id != target)
        sibling.status, sibling.result = "succeeded", {"message": "已完成配乐", "asset": "keep"}
        run, impact, gid = received(db, ids, rid, anchor={"task_ids": [str(target)]})
        command(db, ids, rid, "pause")
        assert guidance.resolve(db, impact, decision(gid, task_ids=[target]))
        assert run.status == "paused" and not guidance.pending(run)
        assert sibling.status == "succeeded" and sibling.result["asset"] == "keep"
        assert db.get(ProductionTask, target).status == "superseded"
        assert not service.finish_attempt(db, old, status="succeeded", result={"asset": "late"})
        active, _ = effective_facts(run.context["facts"])
        assert len(active) == 1 and active[0].text == "开场雨太大了，只调整开场"
        assert active[0].evidence["task_ids"] == [str(target)]


def test_fabricated_evidence_and_outside_selection_never_steer(studio):
    sessions, ids, rid = seeded(studio, plan(("opening", {}), ("music", {})))
    with sessions() as db:
        rows = service.tasks(db, db.get(ProductionRun, rid))
        run, impact, gid = received(db, ids, rid, anchor={"task_ids": [str(rows[0].id)]})
        for wrong in [
            decision(gid, task_ids=[rows[0].id], text="用户没说过"),
            decision(gid, task_ids=[rows[1].id]),
            decision(gid, task_ids=[uuid.uuid4()]),
            decision(gid),
        ]:
            with pytest.raises(Conflict):
                guidance.resolve(db, impact, wrong)
        assert all(t.status == "queued" for t in rows)
        assert guidance.pending(run) == gid


def test_ambiguous_question_collects_clarification_and_old_assessment_cannot_return(studio):
    sessions, ids, rid = seeded(studio, plan(("opening", {}), ("ending", {})))
    with sessions() as db:
        run, first, gid = received(db, ids, rid, text="这段不太对")
        assert guidance.resolve(
            db, first, decision(gid, "question", message="指的是开场还是结尾？")
        )
        assert guidance.pending(run) and run.status == "needs_attention"
        run, latest, next_id = received(db, ids, rid, text="是开场，下雨不要这么大")
        task = db.get(ProductionTask, uuid.UUID(latest["task_id"]))
        packet = guidance.prepare(db, run, task)
        assert [m["text"] for m in packet["messages"]] == ["这段不太对", "是开场，下雨不要这么大"]
        assert packet["messages"][0]["sequence"] < packet["messages"][1]["sequence"]
        assert not guidance.resolve(db, first, decision(gid, "discussion"))
        assert guidance.pending(run) == next_id


def test_global_change_requires_idempotent_human_confirmation(studio):
    sessions, ids, rid = seeded(studio, plan(("opening", {}), ("music", {})))
    with sessions() as db:
        rows = service.tasks(db, db.get(ProductionRun, rid))
        run, impact, gid = received(db, ids, rid, text="整个剧本推倒重来")
        global_change = decision(gid, "global", text="整个剧本推倒重来", task_ids=[rows[0].id])
        assert guidance.resolve(db, impact, global_change)
        assert all(t.status == "queued" for t in rows)
        assert run.context["guidance_inbox"][-1]["status"] == "awaiting_confirmation"
        key, rev = uuid.uuid4(), run.revision
        guidance.confirm(db, context(db, ids), rid, uuid.UUID(gid), key, rev)
        assert all(t.status == "superseded" for t in rows)
        assert run.context["facts"][-1]["scope"] == "project"
        before = len(service.tasks(db, run))
        guidance.confirm(db, context(db, ids), rid, uuid.UUID(gid), key, rev)
        assert len(service.tasks(db, run)) == before and not guidance.pending(run)


def test_retract_invalidates_dependent_summaries_without_recreating_sibling(studio):
    sessions, ids, rid = seeded(studio, plan(("opening", {}), ("music", {})))
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        target, sibling = service.tasks(db, run)
        run, impact, gid = received(db, ids, rid)
        guidance.resolve(db, impact, decision(gid, task_ids=[target.id]))
        fact = copy.deepcopy(run.context["facts"][-1])
        run.context = {
            **run.context,
            "facts": [
                *run.context["facts"],
                {
                    "id": "summary",
                    "kind": "summary",
                    "text": "已经决定减少开场雨量",
                    "source_id": "summary",
                    "sequence": run.event_sequence,
                    "depends_on": [fact["id"]],
                },
            ],
        }
        run, impact, gid = received(db, ids, rid, text="撤回刚才减少雨量的要求")
        retract = decision(
            gid,
            "retract",
            supersedes=[fact["id"]],
            evidence=[
                {
                    "decision_id": fact["id"],
                    "quote": "撤回刚才减少雨量的要求",
                }
            ],
        )
        assert guidance.resolve(db, impact, retract)
        active, omitted = effective_facts(run.context["facts"])
        assert fact["id"] not in {f.id for f in active}
        assert "summary" in {f["id"] for f in omitted}
        assert sibling.status == "queued"
        assert run.context["facts"][0]["status"] == "withdrawn"


def test_waiting_business_question_receives_answer_without_replanning(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        task = service.tasks(db, run)[0]
        task.status = "waiting_input"
        task.state = {"question": "配乐偏悬疑还是舒缓？"}
        run, impact, gid = received(db, ids, rid, text="用悬疑的音乐")
        assert guidance.resolve(
            db, impact, decision(gid, text="用悬疑的音乐", task_ids=[task.id], operation="reply")
        )
        assert task.status == "queued" and task.state["answer"] == "用悬疑的音乐"
        assert len(service.tasks(db, run)) == 2


def test_replace_then_retract_does_not_resurrect_earlier_user_instruction(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        target = service.tasks(db, run)[0]
        run, impact, gid = received(db, ids, rid, text="主角改穿红衣")
        guidance.resolve(db, impact, decision(gid, task_ids=[target.id], text="改穿红衣"))
        red = run.context["facts"][-1]["id"]
        current = next(
            t
            for t in service.tasks(db, run)
            if not guidance.is_impact(t) and t.status != "superseded"
        )
        run, impact, gid = received(db, ids, rid, text="不要红衣了，改穿黑衣")
        guidance.resolve(
            db,
            impact,
            decision(
                gid,
                task_ids=[current.id],
                text="不要红衣了，改穿黑衣",
                supersedes=[red],
            ),
        )
        black = run.context["facts"][-1]["id"]
        assert [f.id for f in effective_facts(run.context["facts"])[0]] == [black]
        run, impact, gid = received(db, ids, rid, text="撤回刚才黑衣的要求")
        guidance.resolve(
            db,
            impact,
            decision(
                gid,
                "retract",
                supersedes=[black],
                evidence=[
                    {
                        "decision_id": black,
                        "quote": "撤回刚才黑衣的要求",
                    }
                ],
            ),
        )
        active = effective_facts(run.context["facts"])[0]
        assert not {red, black} & {fact.id for fact in active}
        assert len(active) == 1 and active[0].text == "撤回刚才黑衣的要求"


def test_new_engineering_baseline_rejects_stale_impact(studio):
    from app.models.work import CreativeWork

    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        work = CreativeWork(project_id=run.project_id, created_by=ids["user"], title="测试作品")
        db.add(work)
        db.flush()
        run.work_id = work.id
        task = service.tasks(db, run)[0]
        run, impact, gid = received(db, ids, rid)
        work.engineering_revision += 1
        db.flush()
        with pytest.raises(Conflict):
            guidance.resolve(db, impact, decision(gid, task_ids=[task.id]))
        assert task.status == "queued" and guidance.pending(run) == gid


def test_real_runtime_uses_impact_contract_and_keeps_pause(studio, monkeypatch):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        run, ticket, gid = received(db, ids, rid, text="先聊聊风格")
        command(db, ids, rid, "pause")
        db.commit()

    def assess(config, packet, media_parts):
        assert packet["guidance"]["guidance_id"] == gid
        return {
            "kind": "guidance",
            "guidance_id": gid,
            "action": "discussion",
            "message": "可以先聊风格，制作继续保持暂停。",
        }

    monkeypatch.setitem(kernel._KERNELS, "fixture", assess)
    runtime.execute(ticket, sessions)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        assert run.status == "paused" and not guidance.pending(run)
        assert db.get(ProductionAttempt, uuid.UUID(ticket["attempt_id"])).status == "succeeded"
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProductionEvent)
                .where(ProductionEvent.kind == "guidance.assessed")
            )
            == 1
        )
