"""Durable production state acceptance; no external models or provider charges."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import Conflict, Forbidden, NotFound
from app.models.harness import ProductionAttempt, ProductionEvent, ProductionRun, ProductionTask
from app.modules.harness import schemas, service
from tests.test_harness_context import context
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def registered_fixture_kernel(monkeypatch):
    from app.modules.harness import kernel

    def unexpected_decision(*_):
        pytest.fail("Test must explicitly provide its decision adapter")

    monkeypatch.setitem(kernel._KERNELS, "fixture", unexpected_decision)


def plan(*specs):
    return schemas.Plan(
        summary="制作测试", tasks=[schemas.TaskSpec(goal=key, key=key, **kw) for key, kw in specs]
    )


def seeded(studio, specs=None, initial_context=None, **policy):
    _, sessions, ids = studio
    with sessions() as db:
        run = ProductionRun(
            project_id=uuid.UUID(ids["project"]),
            created_by=ids["user"],
            request_key=uuid.uuid4(),
            goal="制作一段可编辑短片",
            engine="fixture",
            status="queued",
            revision=1,
            event_sequence=0,
            policy=schemas.Policy(**policy).model_dump(),
            context=initial_context or {},
            plan={},
            result={},
            error={},
        )
        db.add(run)
        db.flush()
        service.add_plan(db, run, specs or plan(("first", {})))
        db.commit()
        return sessions, ids, run.id


def command(db, ids, run_id, action, **kw):
    run = db.get(ProductionRun, run_id)
    data = schemas.Control(
        request_key=kw.pop("request_key", uuid.uuid4()),
        expected_revision=kw.pop("expected_revision", run.revision),
        action=action,
        **kw,
    )
    return service.control(db, context(db, ids), run_id, data)


@pytest.mark.parametrize(
    "specs",
    [
        [("a", {"dependencies": ["missing"]})],
        [("a", {"dependencies": ["b"]}), ("b", {"dependencies": ["a"]})],
        [("a", {}), ("a", {})],
    ],
)
def test_invalid_graph_is_rejected_before_execution(specs):
    with pytest.raises(ValidationError):
        plan(*specs)


def test_parallel_claims_honor_limit_and_dependency_order(studio):
    sessions, _, rid = seeded(
        studio, plan(("a", {}), ("b", {}), ("join", {"dependencies": ["a", "b"]})), max_parallel=2
    )

    def take():
        with sessions() as db:
            ticket = service.claim(db, rid)
            db.commit()
            return ticket

    with ThreadPoolExecutor(max_workers=4) as pool:
        tickets = [t for t in pool.map(lambda _: take(), range(4)) if t]
    assert len(tickets) == 2
    assert len({t["task_id"] for t in tickets}) == 2
    with sessions() as db:
        service.finish_attempt(db, tickets[0], status="succeeded", result={"artifact": "a"})
        db.commit()
        assert service.claim(db, rid) is None  # join still awaits second prerequisite
        service.finish_attempt(db, tickets[1], status="succeeded", result={"artifact": "b"})
        db.commit()
        joined = service.claim(db, rid)
        assert db.get(ProductionTask, uuid.UUID(joined["task_id"])).key == "join"
        service.finish_attempt(db, joined, status="succeeded", result={"artifact": "film"})
        db.commit()
        assert db.get(ProductionRun, rid).status == "succeeded"


def test_pause_allows_receipt_but_prevents_next_dispatch_until_resume(studio):
    sessions, ids, rid = seeded(studio, plan(("a", {}), ("b", {"dependencies": ["a"]})))
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
        command(db, ids, rid, "pause")
        db.commit()
        assert service.finish_attempt(db, ticket, status="succeeded", result={"saved": True})
        db.commit()
        assert db.get(ProductionRun, rid).status == "paused"
        assert service.claim(db, rid) is None
    # A new process/session restores state from the database, not old Python objects.
    with sessions() as db:
        assert db.get(ProductionRun, rid).status == "paused"
        command(db, ids, rid, "resume")
        db.commit()
        ticket = service.claim(db, rid)
        assert db.get(ProductionTask, uuid.UUID(ticket["task_id"])).key == "b"


def test_cancel_fences_late_results_and_retains_receipt(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
        command(db, ids, rid, "cancel")
        db.commit()
        assert (
            service.finish_attempt(db, ticket, status="succeeded", result={"late": "asset"})
            is False
        )
        db.commit()
        assert db.get(ProductionRun, rid).status == "canceled"
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        attempt = db.get(ProductionAttempt, uuid.UUID(ticket["attempt_id"]))
        assert task.status == "canceled" and task.result == {}
        assert attempt.status == "late_result" and attempt.result == {"late": "asset"}


def test_local_steering_preserves_unrelated_work_and_fences_old_attempt(studio):
    sessions, ids, rid = seeded(
        studio, plan(("image", {}), ("music", {}), ("join", {"dependencies": ["image", "music"]}))
    )
    with sessions() as db:
        first, second = service.claim(db, rid), service.claim(db, rid)
        by_key = {db.get(ProductionTask, uuid.UUID(t["task_id"])).key: t for t in (first, second)}
        service.finish_attempt(db, by_key["music"], status="succeeded", result={"music": "keep"})
        db.commit()
        command(
            db,
            ids,
            rid,
            "steer",
            task_ids=[uuid.UUID(by_key["image"]["task_id"])],
            message="只把画面改成夜景，保留音乐",
        )
        db.commit()
        rows = {t.key: t for t in service.tasks(db, db.get(ProductionRun, rid))}
        assert rows["image"].status == rows["join"].status == "superseded"
        assert rows["music"].status == "succeeded" and rows["music"].result == {"music": "keep"}
        assert (
            service.finish_attempt(db, by_key["image"], status="succeeded", result={"image": "old"})
            is False
        )
        assert rows["image"].result == {}
        assert any(t.key.startswith("revision_") and t.status == "queued" for t in rows.values())


def test_expired_lease_is_uncertain_and_never_blindly_replayed(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.get(ProductionTask, uuid.UUID(ticket["task_id"])).lease_expires_at = (
            service.now() - timedelta(seconds=1)
        )
        db.commit()
    with sessions() as db:
        assert service.claim(db, rid) is None
        db.commit()
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert task.status == "uncertain" and task.attempts == 1
        assert task.error["retryable"] is False
        with pytest.raises(Conflict):
            command(db, ids, rid, "retry")
        assert (
            service.finish_attempt(db, ticket, status="succeeded", result={"provider": "accepted"})
            is False
        )
        assert task.status == "uncertain"


def test_control_request_id_is_idempotent_and_reuse_is_rejected(studio):
    sessions, ids, rid = seeded(studio)
    key = uuid.uuid4()
    with sessions() as db:
        first = command(db, ids, rid, "pause", request_key=key, expected_revision=1)
        db.commit()
        again = command(db, ids, rid, "pause", request_key=key, expected_revision=1)
        assert first["revision"] == again["revision"]
        assert first["event_sequence"] == again["event_sequence"]
        with pytest.raises(Conflict):
            command(db, ids, rid, "resume", request_key=key)


def test_revoked_membership_prevents_new_dispatch(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        ctx = context(db, ids)
        db.delete(ctx.membership)
        db.commit()
    with sessions() as db:
        with pytest.raises(Forbidden):
            service.claim(db, rid)
        assert db.get(ProductionRun, rid).status == "queued"


def test_pause_resume_does_not_bypass_plan_approval(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        coordinator = service.tasks(db, run)[0]
        coordinator.status = "waiting_plan"
        run.status = "needs_plan_approval"
        run.plan = plan(("approved_child", {})).model_dump(mode="json")
        db.commit()
        command(db, ids, rid, "pause")
        db.commit()
        command(db, ids, rid, "resume")
        db.commit()
        assert run.status == "needs_plan_approval"
        assert service.claim(db, rid) is None
        command(db, ids, rid, "approve_plan")
        db.commit()
        ticket = service.claim(db, rid)
        assert db.get(ProductionTask, uuid.UUID(ticket["task_id"])).parent_id == coordinator.id


def test_retrying_failed_child_unblocks_waiting_parent(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        parent = service.tasks(db, run)[0]
        service.add_plan(db, run, plan(("child", {})), parent)
        db.commit()
        ticket = service.claim(db, rid)
        service.finish_attempt(
            db, ticket, status="failed", error={"code": "TEMPORARY", "retryable": True}
        )
        db.commit()
        assert parent.status == "blocked"
        command(db, ids, rid, "retry")
        db.commit()
        retry = service.claim(db, rid)
        service.finish_attempt(db, retry, status="succeeded", result={"child": "done"})
        db.commit()
        assert parent.status == "queued"
        assert parent.state["child_results"][0]["result"] == {"child": "done"}


def test_duplicate_receipt_is_idempotent_and_ticket_is_bound_to_task(studio):
    sessions, _, rid = seeded(studio, plan(("a", {}), ("b", {})))
    with sessions() as db:
        first, second = service.claim(db, rid), service.claim(db, rid)
        assert service.finish_attempt(db, first, status="succeeded", result={"once": True})
        db.commit()
        count = len(list(db.scalars(select(ProductionEvent).where(ProductionEvent.run_id == rid))))
        service.finish_attempt(db, first, status="succeeded", result={"once": True})
        db.commit()
        assert db.get(ProductionAttempt, uuid.UUID(first["attempt_id"])).status == "succeeded"
        assert (
            len(list(db.scalars(select(ProductionEvent).where(ProductionEvent.run_id == rid))))
            == count
        )
        forged = {**second, "task_id": first["task_id"]}
        with pytest.raises((NotFound, Conflict)):
            service.finish_attempt(db, forged, status="succeeded", result={"wrong": True})


def test_create_idempotency_preserves_fixed_sources_and_work_link(studio, monkeypatch):
    import io

    from PIL import Image

    from app.modules.harness import kernel
    from tests.test_harness_context import seed
    from tests.test_media_workflow import post_ok

    client, sessions, ids = studio
    monkeypatch.setitem(
        kernel._KERNELS, "fixture", lambda *_: {"kind": "finish", "message": "done"}
    )
    work = post_ok(client, f"/api/v1/projects/{ids['project']}/works", {"title": "Harness work"})
    with sessions() as db:
        stream = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(stream, "PNG")
        reference = seed(db, ids, stream.getvalue(), "image", "image/png")
        data = schemas.Create(
            goal="制作",
            engine="fixture",
            request_key=uuid.uuid4(),
            work_id=work["id"],
            references=[reference],
        )
        first = service.create(db, context(db, ids), data)
        db.commit()
        repeat = service.create(db, context(db, ids), data)
        assert repeat["id"] == first["id"]
        assert repeat["work_id"] == uuid.UUID(work["id"])
        run = db.get(ProductionRun, first["id"])
        assert run.context["references"][0]["expected_blob_hash"]
        with pytest.raises(Conflict):
            service.create(db, context(db, ids), data.model_copy(update={"goal": "另一个制作"}))


def test_kernel_swap_delegation_and_real_read_tool(studio, monkeypatch):
    from app.modules.harness import kernel, runtime

    _, sessions, ids = studio
    approved = plan(("producer", {}))
    monkeypatch.setitem(
        kernel._KERNELS,
        "fixture",
        lambda *_: {"kind": "plan", "plan": approved.model_dump(mode="json")},
    )
    with sessions() as db:
        created = service.create(
            db,
            context(db, ids),
            schemas.Create(goal="了解当前项目再汇总", engine="fixture", request_key=uuid.uuid4()),
        )
        db.commit()
        rid = created["id"]
        first = service.claim(db, rid)
        db.commit()
    runtime.execute(first, sessions)
    with sessions() as db:
        assert db.get(ProductionRun, rid).status == "needs_plan_approval"
        assert service.claim(db, rid) is None
        command(db, ids, rid, "approve_plan")
        db.commit()
    decisions = []

    def second_kernel(config, packet, media):
        assert not config and not media
        decisions.append(packet["task"]["key"])
        if packet["task"]["key"] == "producer" and not packet["child_results"]:
            return {
                "kind": "delegate",
                "plan": plan(
                    (
                        "inspect",
                        {
                            "kind": "tool",
                            "tool": "project.overview",
                            "expected_output": "observation",
                        },
                    )
                ).model_dump(mode="json"),
            }
        assert packet["child_results"]
        return {"kind": "finish", "message": "已根据实际项目信息完成汇总"}

    # Replacement has no hidden dialogue memory; it reconstructs from persisted state.
    monkeypatch.setitem(kernel._KERNELS, "fixture", second_kernel)
    for _ in range(8):
        with sessions() as db:
            ticket = service.claim(db, rid)
            db.commit()
        if not ticket:
            break
        runtime.execute(ticket, sessions)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        assert run.status == "succeeded", service.out(db, run)
        rows = service.tasks(db, run)
        assert len(rows) == 3
        actual = next(t for t in rows if t.kind == "tool")
        assert actual.result["tool"] == "project.overview"
        assert actual.result["status"] == "succeeded" and actual.result["data"]
        assert decisions == ["producer", "producer", "coordinator"]


def test_finish_without_actual_artifact_fails(studio, monkeypatch):
    from app.modules.harness import kernel, runtime

    sessions, _, rid = seeded(studio, plan(("make_film", {"expected_output": "artifact"})))
    monkeypatch.setitem(
        kernel._KERNELS,
        "fixture",
        lambda *_: {"kind": "finish", "message": "已经完成视频", "artifacts": [{"id": "invented"}]},
    )
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        assert run.status == "failed"
        task = service.tasks(db, run)[0]
        assert task.result == {}
        assert task.error["code"] == "VERSION_CONFLICT"


def test_queued_generation_receipt_is_not_a_finished_artifact(studio, monkeypatch):
    from app.modules.harness import kernel, runtime

    sessions, _, rid = seeded(studio, plan(("make_film", {"expected_output": "artifact"})))
    monkeypatch.setitem(
        kernel._KERNELS, "fixture", lambda *_: {"kind": "finish", "message": "已经完成视频"}
    )
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        task = service.tasks(db, run)[0]
        # A successful submit is not a successful generation, even when the kernel says so.
        task.state = {
            "steps": [
                {
                    "receipt": {
                        "tool": "media.generate",
                        "status": "succeeded",
                        "effect": "generate",
                        "data": {"job_id": str(uuid.uuid4()), "status": "pending"},
                    }
                }
            ]
        }
        db.commit()
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions)
    with sessions() as db:
        assert db.get(ProductionRun, rid).status != "succeeded"


def test_long_read_tool_does_not_block_pause(studio, monkeypatch):
    from app.modules.harness import runtime

    sessions, ids, rid = seeded(
        studio,
        plan(
            (
                "inspect",
                {"kind": "tool", "tool": "project.overview", "expected_output": "observation"},
            )
        ),
    )
    entered, release, paused = Event(), Event(), Event()
    actual_invoke = runtime.invoke

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(8)
        return actual_invoke(*args, **kwargs)

    def pause():
        with sessions() as db:
            command(db, ids, rid, "pause")
            db.commit()
        paused.set()

    monkeypatch.setattr(runtime, "invoke", delayed)
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(runtime.execute, ticket, sessions)
        assert entered.wait(5)
        control = pool.submit(pause)
        try:
            responsive = paused.wait(2)
        finally:
            release.set()
        execution.result(timeout=8)
        control.result(timeout=8)
    assert responsive, "耗时媒体读取持有整个run锁，暂停和其他Agent均被阻塞"
    with sessions() as db:
        assert db.get(ProductionRun, rid).status == "paused"


def test_local_steer_of_delegated_child_does_not_leave_obsolete_parent_blocking(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        parent = service.tasks(db, run)[0]
        service.add_plan(db, run, plan(("picture", {}), ("sound", {})), parent)
        db.commit()
        children = [t for t in service.tasks(db, run) if t.parent_id]
        for child in children:
            child.status, child.result = "succeeded", {"name": child.key}
        db.commit()
        changed = children[0]
        command(db, ids, rid, "steer", task_ids=[changed.id], message="只重做这一部分")
        db.commit()
        current = service.tasks(db, run)
        assert parent.status != "blocked", "旧汇总父任务仍依赖被替代子任务，会永久阻碍新计划完成"
        assert children[1].status == "succeeded"  # unrelated child result is retained
        assert any(t.key.startswith("revision_") for t in current)


def test_repeated_pause_keeps_original_resume_state(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        command(db, ids, rid, "pause")
        db.commit()
        try:
            command(db, ids, rid, "pause")  # another client / request ID
        except Conflict:
            pass  # Rejecting an already-paused request must retain its original resume state.
        db.commit()
        command(db, ids, rid, "resume")
        db.commit()
        assert db.get(ProductionRun, rid).status in {"queued", "running"}
        assert service.claim(db, rid)


def test_guidance_during_pause_does_not_resume_production(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        command(db, ids, rid, "pause")
        db.commit()
        command(db, ids, rid, "steer", message="等我回来再做，先把人物服装要求改为黑色")
        db.commit()
        assert db.get(ProductionRun, rid).status == "paused"
        assert service.claim(db, rid) is None
        command(db, ids, rid, "resume")
        db.commit()
        assert service.claim(db, rid)


def test_local_guidance_reaches_replanning_task_but_not_unrelated_task(studio):
    from app.modules.harness import runtime

    sessions, ids, rid = seeded(studio, plan(("image", {}), ("music", {})))
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        image = next(t for t in service.tasks(db, run) if t.key == "image")
        command(db, ids, rid, "steer", task_ids=[image.id], message="只把这个镜头调暗")
        db.commit()
        ticket = service.claim(db, rid)
        db.commit()
        assert db.get(ProductionTask, uuid.UUID(ticket["task_id"])).key == "music"
        prepared = runtime.prepare(db, ticket)
        assert prepared["packet"]["context"]["facts"]["user_decision"] == []
        service.finish_attempt(db, ticket, status="succeeded", result={"music": "kept"})
        db.commit()
        revision = service.claim(db, rid)
        db.commit()
        prepared = runtime.prepare(db, revision)
        decisions = prepared["packet"]["context"]["facts"]["user_decision"]
        assert [f["text"] for f in decisions] == ["只把这个镜头调暗"]


@pytest.mark.parametrize("decision", ["approve_tool", "reject_tool"])
def test_reviewed_direct_tool_never_replays_accepted_or_rejected_edit(studio, decision):
    from app.models.shot import Shot
    from app.modules.harness import runtime

    _, _, ids = studio
    sessions, ids, rid = seeded(
        studio,
        plan(
            (
                "rename",
                {
                    "kind": "tool",
                    "tool": "object.propose_edit",
                    "arguments": {
                        "target_type": "shot",
                        "target_id": ids["shot"],
                        "summary": "让镜头名称容易辨认",
                        "ops": [{"op": "set_field", "field": "title", "value": "雨夜对峙"}],
                    },
                    "expected_output": "candidate",
                },
            )
        ),
        initial_context={"scope": [{"target_type": "shot", "target_id": ids["shot"]}]},
    )
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions)
    with sessions() as db:
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert task.status == "waiting_review"
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "Test shot"
        with pytest.raises(Conflict):
            command(db, ids, rid, decision, task_ids=[task.id], review_id=uuid.uuid4())
        db.rollback()
        command(
            db, ids, rid, decision, task_ids=[task.id], review_id=uuid.UUID(task.state["review_id"])
        )
        db.commit()
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == (
            "雨夜对峙" if decision == "approve_tool" else "Test shot"
        )
        assert service.claim(db, rid) is None, "审阅后的直接工具任务不能重放相同提案"


def test_media_reinspection_before_kernel_does_not_block_pause(studio, monkeypatch):
    from app.modules.harness import kernel, media_context, runtime

    sessions, ids, rid = seeded(studio)
    entered, release, paused = Event(), Event(), Event()

    def delayed_inspection(*args, **kwargs):
        entered.set()
        assert release.wait(8)
        return {"content_parts": []}

    def pause():
        with sessions() as db:
            command(db, ids, rid, "pause")
            db.commit()
        paused.set()

    monkeypatch.setattr(media_context, "inspect_media", delayed_inspection)
    monkeypatch.setitem(
        kernel._KERNELS, "fixture", lambda *_: {"kind": "finish", "message": "已整理"}
    )
    with sessions() as db:
        task = service.tasks(db, db.get(ProductionRun, rid))[0]
        task.state = {"inspection": {"reference": {"kind": "generation", "id": str(uuid.uuid4())}}}
        db.commit()
        ticket = service.claim(db, rid)
        db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(runtime.execute, ticket, sessions)
        assert entered.wait(5)
        control = pool.submit(pause)
        try:
            responsive = paused.wait(2)
        finally:
            release.set()
        execution.result(timeout=8)
        control.result(timeout=8)
    assert responsive, "模型上下文二次解码持有run锁，阻塞用户暂停"
    with sessions() as db:
        assert db.get(ProductionRun, rid).status == "paused"
