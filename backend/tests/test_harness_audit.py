"""Cross-boundary production invariants exercised against durable domain state."""

# ruff: noqa: F811
import copy
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.core.errors import Conflict
from app.models.generation import GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.modules.harness import kernel, runtime, schemas, service
from app.modules.tools import registry
from tests.test_harness_context import context
from tests.test_harness_job_wait import prepared
from tests.test_harness_runtime import command, plan, seeded
from tests.test_media_workflow import studio  # noqa: F401


def delegated(studio):
    _, _, ids = studio
    scope = [{"target_type": "shot", "target_id": ids["shot"]}]
    sessions, ids, rid = seeded(
        studio,
        plan(("visual", {}), ("audio", {}), ("unrelated", {})),
        initial_context={"scope": scope},
    )
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        roots = {t.key: t for t in service.tasks(db, run)}
        for name in ("visual", "audio"):
            service.add_plan(
                db,
                run,
                plan(
                    ("change", {"scope": [], "allowed_tools": ["project.overview"]}),
                    ("keep", {}),
                ),
                roots[name],
            )
        db.commit()
    return sessions, ids, rid


def test_steer_multiple_parents_keeps_unique_children_and_exact_dependencies(studio):
    sessions, ids, rid = delegated(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        rows = service.tasks(db, run)
        parents = {t.key: t for t in rows if not t.parent_id}
        children = {
            name: next(t for t in rows if t.parent_id == parent.id and t.key.endswith(".change"))
            for name, parent in parents.items()
            if name != "unrelated"
        }
        # Independent subplans can be reconnected by orchestration; the consumer
        # must wait for the new visual result rather than obsolete or unrelated work.
        children["audio"].dependencies = [str(children["visual"].id)]
        before = {t.id: copy.deepcopy(t.state) for t in rows}
        db.commit()
        command(db, ids, rid, "steer", task_ids=[children["visual"].id], message="修正动作并重配音")
        db.commit()
        current = service.tasks(db, run)
        replacements = [t for t in current if t.state.get("replaces")]
        assert len(replacements) == 2
        by_parent = {t.parent_id: t for t in replacements}
        assert set(by_parent) == {parents["visual"].id, parents["audio"].id}
        for name in ("visual", "audio"):
            parent = parents[name]
            replacement = by_parent[parent.id]
            expected_children = [
                str(replacement.id) if c == str(children[name].id) else c
                for c in before[parent.id]["children"]
            ]
            assert parent.state["children"] == expected_children
            assert replacement.specification["scope"] == []
            assert replacement.specification["allowed_tools"] == ["project.overview"]
        assert by_parent[parents["audio"].id].dependencies == [
            str(by_parent[parents["visual"].id].id)
        ]
        assert parents["unrelated"].state == before[parents["unrelated"].id]
        assert parents["unrelated"].status == "queued"


def test_steer_parent_and_descendant_replaces_one_boundary_without_grant_expansion(studio):
    sessions, ids, rid = delegated(studio)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        parent = next(t for t in service.tasks(db, run) if t.key == "visual")
        parent.specification = {
            **parent.specification,
            "scope": [],
            "allowed_tools": ["project.overview"],
        }
        children = [t for t in service.tasks(db, run) if t.parent_id == parent.id]
        db.commit()
        command(
            db, ids, rid, "steer", task_ids=[parent.id, children[0].id], message="重新安排这部分"
        )
        db.commit()
        replacements = [t for t in service.tasks(db, run) if t.state.get("replaces")]
        assert len(replacements) == 1
        replacement = replacements[0]
        assert replacement.parent_id is None
        assert set(replacement.state["replaces"]) == {
            str(parent.id),
            *(str(t.id) for t in children),
        }
        assert replacement.specification["scope"] == []
        assert replacement.specification["allowed_tools"] == ["project.overview"]


def test_steer_leaf_invalidates_ancestor_consumers_but_preserves_siblings(studio):
    sessions, ids, rid = seeded(
        studio, plan(("chapter", {}), ("final", {"dependencies": ["chapter"]}), ("music", {}))
    )
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        roots = {t.key: t for t in service.tasks(db, run)}
        service.add_plan(db, run, plan(("scene", {})), roots["chapter"])
        scene = next(t for t in service.tasks(db, run) if t.parent_id == roots["chapter"].id)
        service.add_plan(db, run, plan(("leaf", {}), ("keep", {})), scene)
        rows = service.tasks(db, run)
        leaf = next(t for t in rows if t.key.endswith(".leaf"))
        keep = next(t for t in rows if t.key.endswith(".keep"))
        for task in rows:
            task.status, task.result = "succeeded", {"saved": task.key}
        scene.state = {**scene.state, "pending_action": {"kind": "finish", "message": "旧汇总"}}
        run.status = "succeeded"
        db.commit()
        command(db, ids, rid, "steer", task_ids=[leaf.id], message="只替换此处画面，重新合成")
        db.commit()
        assert roots["final"].status == "superseded", "旧汇总的消费者没有失效"
        assert roots["chapter"].status == scene.status == "waiting_children"
        assert "pending_action" not in scene.state
        assert keep.status == roots["music"].status == "succeeded"
        replacements = [t for t in service.tasks(db, run) if t.state.get("replaces")]
        assert len(replacements) == 2
        final = next(t for t in replacements if str(roots["final"].id) in t.state["replaces"])
        assert final.dependencies == [str(roots["chapter"].id)]


@pytest.mark.parametrize("drift", ["version", "schema"])
def test_frozen_tool_drift_blocks_real_runtime_before_handler(studio, monkeypatch, drift):
    sessions, _, rid = seeded(
        studio, plan(("inspect", {"kind": "tool", "tool": "project.overview"}))
    )
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    original = registry.CATALOG["project.overview"]
    if drift == "version":
        registry.CATALOG["project.overview"] = replace(original, version=original.version + 1)
    else:
        registry.CATALOG["project.overview"] = replace(original, schema=registry.IdArgs)
    registry._HANDLERS["project.overview"] = lambda *_: pytest.fail("drifted handler executed")
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions)
    with sessions() as db:
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert task.status == "failed"
        assert "契约" in task.error["message"]
        assert task.state.get("steps", []) == []


def test_pause_resume_never_resubmits_unknown_paid_attempt(studio, monkeypatch):
    sessions, ids, rid, tid, jid, _ = prepared(studio, task_status="uncertain")
    from app.modules.generation import jobs

    monkeypatch.setattr(jobs, "dispatch", lambda *_: pytest.fail("uncertain request resubmitted"))
    with sessions() as db:
        # A different persisted receipt is not evidence about the lost submission.
        job = db.get(GenerationJob, jid)
        job.input_snapshot = {
            "harness_origin": {
                "run_id": str(rid),
                "task_id": str(tid),
                "request_key": str(uuid.uuid4()),
            }
        }
        db.commit()
        command(db, ids, rid, "pause")
        db.commit()
        assert service.claim(db, rid) is None
        assert db.get(ProductionRun, rid).status == "paused"
    with sessions() as db:
        command(db, ids, rid, "resume")
        db.commit()
        assert service.claim(db, rid) is None
        assert db.get(ProductionTask, tid).status == "uncertain"
        assert db.get(ProductionRun, rid).status == "needs_attention"
        with pytest.raises(Conflict):
            command(db, ids, rid, "retry", task_ids=[tid])
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1


def test_two_conversations_same_work_keep_late_candidate_and_protect_adopted_version(
    studio, monkeypatch
):
    from tests.test_engineering import accept, setup
    from tests.test_harness_conversation import conversation
    from tests.test_harness_engineering import propose, put, rebase

    client, sessions, ids, root, wid, _ = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    doc = put()
    accept(client, path, propose(client, path, 0, [doc]))
    monkeypatch.setitem(
        kernel._KERNELS, "fixture", lambda *_: pytest.fail("tool tasks need no model")
    )
    run_ids = []
    conversation_ids = []
    with sessions() as db:
        for width in (32, 64):
            row = conversation(db, ids)
            created = service.create(
                db,
                context(db, ids),
                schemas.Create(
                    request_key=uuid.uuid4(),
                    conversation_id=row.id,
                    work_id=wid,
                    engine="fixture",
                    goal=f"将画布宽度改成 {width}",
                    plan=plan(
                        (
                            "candidate",
                            {
                                "kind": "tool",
                                "tool": "engineering.propose",
                                "expected_output": "candidate",
                                "arguments": {
                                    "work_id": wid,
                                    "request_key": str(uuid.uuid4()),
                                    "base_revision": 1,
                                    "commands": [put(doc["document_id"], 1, width)],
                                },
                            },
                        )
                    ),
                ),
            )
            rid = uuid.UUID(str(created["id"]))
            command(db, ids, rid, "approve_plan")
            run_ids.append(rid)
            conversation_ids.append(row.id)
        db.commit()
    changes = []
    for index, rid in enumerate(run_ids):
        with sessions() as db:
            ticket = service.claim(db, rid)
            db.commit()
        runtime.execute(ticket, sessions)
        with sessions() as db:
            run = db.get(ProductionRun, rid)
            task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
            assert task.status == "succeeded", task.error
            assert run.conversation_id == conversation_ids[index]
            changes.append(task.result["data"])
        if index == 0:
            accept(client, path, changes[0])
    assert changes[0]["id"] != changes[1]["id"]
    assert changes[1]["base_revision"] == 1 and changes[1]["status"] == "proposed"
    assert (
        client.post(
            path + f"/changes/{changes[1]['id']}/accept",
            json={"fingerprint": changes[1]["fingerprint"]},
        ).status_code
        == 409
    )
    assert rebase(client, path, changes[1], 2).status_code == 409
    current = client.get(path).json()
    assert current["revision"] == 2
    ref = current["snapshot"]["native_roots"][doc["document_id"]]
    fixed = client.get(path + f"/documents/{ref['id']}/revisions/{ref['revision']}").json()
    assert fixed["payload"]["canvas"]["width"] == 32
