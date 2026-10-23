"""Kernel replacement never changes project state or replays completed production."""

# ruff: noqa: F811
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, Forbidden
from app.models.harness import ProductionEvent, ProductionRun
from app.models.identity import Membership, User
from app.modules.harness import kernel, service
from app.modules.harness.kernel_control import SwitchKernelRequest, switch
from tests.test_harness_context import context
from tests.test_harness_runtime import command, plan, seeded
from tests.test_media_workflow import studio  # noqa: F401


def request(db, rid, engine="mock", **kw):
    return SwitchKernelRequest(
        engine=engine,
        expected_revision=db.get(ProductionRun, rid).revision,
        request_key=kw.pop("request_key", uuid.uuid4()),
        **kw,
    )


def test_switch_requires_explicit_pause_and_settled_lease(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        with pytest.raises(Conflict):
            switch(db, context(db, ids), rid, request(db, rid))
        ticket = service.claim(db, rid)
        db.commit()
        command(db, ids, rid, "pause")
        db.commit()
        with pytest.raises(Conflict):
            switch(db, context(db, ids), rid, request(db, rid))
        assert db.get(ProductionRun, rid).engine == "fixture"
        service.finish_attempt(db, ticket, status="succeeded", result={"kept": True})
        db.commit()
        changed = switch(db, context(db, ids), rid, request(db, rid))
        assert changed["engine"] == "mock" and changed["status"] == "paused"


def test_switch_keeps_completed_work_and_invalidates_only_unexecuted_agent_decision(studio):
    sessions, ids, rid = seeded(studio, plan(("done", {}), ("next", {"dependencies": ["done"]})))
    with sessions() as db:
        ticket = service.claim(db, rid)
        service.finish_attempt(db, ticket, status="succeeded", result={"material": "retain"})
        run = db.get(ProductionRun, rid)
        run.context = {"kernel_binding": "old", "kernel_profile": {"provider_name": "old"}}
        rows = service.tasks(db, run)
        todo = next(task for task in rows if task.key == "next")
        todo.state = {
            "pending_action": {"kind": "finish", "message": "old decision"},
            "loaded_tools": ["project.overview"],
        }
        db.commit()
        command(db, ids, rid, "pause")
        db.commit()
        before = [(t.id, t.revision, t.status, dict(t.result)) for t in rows]
        data = request(db, rid)
        switched = switch(db, context(db, ids), rid, data)
        db.commit()
        assert [(t.id, t.revision, t.status, t.result) for t in rows] == before
        assert todo.state == {"loaded_tools": ["project.overview"]}
        assert switched["status"] == "paused"
        assert "kernel_binding" not in run.context and "kernel_profile" not in run.context
        event = db.scalar(
            select(ProductionEvent).where(ProductionEvent.request_key == data.request_key)
        )
        assert event.payload["previous"] == {"engine": "fixture", "provider": None}
        assert event.payload["discarded_pending_action_tasks"] == [str(todo.id)]
        assert service.claim(db, rid) is None
        command(db, ids, rid, "resume")
        db.commit()
        assert service.claim(db, rid)["task_id"] == str(todo.id)


def test_switch_key_is_idempotent_after_resume_but_cannot_retarget(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        command(db, ids, rid, "pause")
        data = request(db, rid)
        first = switch(db, context(db, ids), rid, data)
        db.commit()
        command(db, ids, rid, "resume")
        db.commit()
        resumed_revision = db.get(ProductionRun, rid).revision
        replay = switch(db, context(db, ids), rid, data)
        assert replay["revision"] == resumed_revision > first["revision"]
        assert replay["status"] != "paused"
        with pytest.raises(Conflict):
            switch(db, context(db, ids), rid, data.model_copy(update={"engine": "cloud_llm"}))


@pytest.mark.parametrize("invalid", ["version", "engine", "adapter_protocol", "provider"])
def test_invalid_kernel_switch_is_atomic_and_never_falls_back(studio, monkeypatch, invalid):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        command(db, ids, rid, "pause")
        db.commit()
        previous = db.get(ProductionRun, rid).revision
        args = {}
        engine = "mock"
        if invalid == "version":
            args["protocol_version"] = 2
        elif invalid == "engine":
            engine = "not_installed"
        elif invalid == "adapter_protocol":

            def bad(*_):
                return {}

            bad.protocol_version = 2
            monkeypatch.setitem(kernel._KERNELS, "incompatible", bad)
            engine = "incompatible"
        else:
            args["provider"] = "unused-profile"
        data = request(db, rid, engine, **args)
        with pytest.raises(CapabilityUnsupported):
            switch(db, context(db, ids), rid, data)
        run = db.get(ProductionRun, rid)
        assert run.engine == "fixture" and run.revision == previous
        assert not db.scalar(
            select(ProductionEvent).where(ProductionEvent.request_key == data.request_key)
        )


def test_cloud_model_configuration_is_validated_without_disclosing_credentials(studio, monkeypatch):
    from app.modules.assist import service as assist

    sessions, ids, rid = seeded(studio)
    secret = "isolated-provider-key-must-not-be-stored"
    checked = []

    def configured(db, project, name, provider, user_id):
        checked.append((project, name, provider, user_id))
        return SimpleNamespace(
            base_url="https://models.example.test/v1", api_key=secret, model="fixture-text"
        )

    monkeypatch.setattr(assist, "_engine", configured)
    with sessions() as db:
        command(db, ids, rid, "pause")
        db.commit()
        data = request(db, rid, "cloud_llm", provider="chosen-model")
        result = switch(db, context(db, ids), rid, data)
        db.commit()
        assert result["engine"] == "cloud_llm" and result["model_provider"] == "chosen-model"
        assert checked == [(uuid.UUID(ids["project"]), "cloud_llm", "chosen-model", ids["user"])]
        events = [event.payload for event in db.scalars(select(ProductionEvent))]
        assert secret not in json.dumps([result, events], default=str)
        monkeypatch.setattr(
            assist, "_engine", lambda *_a, **_kw: SimpleNamespace(base_url="", api_key="", model="")
        )
        with pytest.raises(CapabilityUnsupported):
            switch(db, context(db, ids), rid, request(db, rid, "cloud_llm"))
        assert db.get(ProductionRun, rid).model_provider == "chosen-model"


def test_revoked_actor_and_other_project_admin_cannot_switch(studio):
    sessions, ids, rid = seeded(studio)
    with sessions() as db:
        command(db, ids, rid, "pause")
        db.commit()
        actor = context(db, ids)
        other = User(
            username="other_controller",
            email="other@example.test",
            password_hash="unused",
            display_name="Other",
        )
        db.add(other)
        db.flush()
        db.add(Membership(project_id=uuid.UUID(ids["project"]), user_id=other.id, role="admin"))
        db.commit()
        other_ctx = context(db, {**ids, "user": other.id})
        with pytest.raises(Forbidden):
            switch(db, other_ctx, rid, request(db, rid))
        db.delete(actor.membership)
        db.commit()
        with pytest.raises(Forbidden):
            switch(db, actor, rid, request(db, rid))
