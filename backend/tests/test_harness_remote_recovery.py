"""Independent remote lease audit: process exit, durable recovery and stale workers.

All provider responses are fixtures. The subprocess test really exits after a
durable claim; lease expiry is injected in PostgreSQL rather than waiting 60 s.
"""

# ruff: noqa: F811
import copy
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.adapters.contracts import Capabilities, GenerationResult, Output, generation_registry
from app.core.config import settings
from app.models.engineering import EngineeringDelivery
from app.models.generation import Generation, GenerationJob, ModelChannel, Quota
from app.models.harness import ProductionRun, ProductionTask
from app.models.identity import Membership
from app.modules.engineering import delivery
from app.modules.generation import jobs
from app.modules.harness import recovery_tools
from app.modules.tools import capabilities, registry
from tests.test_harness_recovery_tools import adapter, failed_job, receive
from tests.test_harness_runtime import command
from tests.test_media_workflow import changing_video, studio  # noqa: F401


@pytest.fixture(autouse=True)
def recovery_contract(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    recovery_tools.register_tools()


def lost_worker(studio, monkeypatch, mode="routed", phase="accepted"):
    sessions, ids, rid, tid, jid, cid = failed_job(studio, mode, phase)
    monkeypatch.setattr(delivery, "SessionLocal", sessions)
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        assert delivery.eligible(job), "Remote harness jobs need the durable dispatch contract"
        delivery.enqueue(db, job)
        token = delivery.claim(db, job)
        job.status = "running"
        db.get(EngineeringDelivery, jid).lease_expires_at = datetime.now(UTC) - timedelta(seconds=2)
        db.commit()
    assert delivery.recover_expired() == 1
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        assert job.status == "failed", "Lost remote ownership must not implicitly resubmit"
        assert not delivery.owned(db, jid, token)
    return sessions, ids, rid, tid, jid, cid, token


@pytest.mark.parametrize("mode", ["routed", "personal"])
@pytest.mark.parametrize("phase", ["accepted", "generated"])
def test_lost_remote_lease_requires_explicit_receipt_only_recovery(
    studio, changing_video, monkeypatch, mode, phase
):
    sessions, ids, rid, tid, jid, _, old_token = lost_worker(studio, monkeypatch, mode, phase)
    sent = []
    monkeypatch.setattr(delivery, "publish", lambda job: sent.append(job.id))
    assert delivery.pump() == 0 and sent == []
    counts = adapter(monkeypatch, changing_video)
    with sessions() as db:
        assert recovery_tools.resume_problem(db.get(GenerationJob, jid)) is None
        result = receive(db, ids, rid, tid, jid)
        assert result.status == "succeeded" and result.data["status"] == "succeeded", (
            result.model_dump()
        )
        assert db.get(EngineeringDelivery, jid).lease_token != old_token
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert db.scalar(select(func.count()).select_from(Generation)) == 1
        assert not db.scalar(select(Generation)).is_selected
        assert float(db.scalar(select(Quota)).used_cost) == 7
    assert counts == {"submit": 0, "poll": ["original-remote-123"]}


@pytest.mark.parametrize("phase", ["dispatching", "submission_unknown", "upstream_failed"])
def test_lost_unknown_submission_never_becomes_an_automatic_or_explicit_retry(
    studio, monkeypatch, phase
):
    sessions, ids, rid, tid, jid, _, _ = lost_worker(studio, monkeypatch, phase=phase)
    counts = adapter(monkeypatch, b"")
    monkeypatch.setattr(
        delivery, "publish", lambda job: pytest.fail("unknown remote submission was published")
    )
    assert delivery.pump() == 0
    with sessions() as db:
        assert recovery_tools.resume_problem(db.get(GenerationJob, jid))
        assert receive(db, ids, rid, tid, jid).status == "failed"
        assert db.get(GenerationJob, jid).status == "failed"
        assert db.scalar(select(func.count()).select_from(Generation)) == 0
    assert counts == {"submit": 0, "poll": []}


@pytest.mark.parametrize("change", ["config", "membership"])
def test_lost_lease_does_not_preserve_stale_configuration_or_membership_grants(
    studio, monkeypatch, change
):
    sessions, ids, rid, tid, jid, cid, _ = lost_worker(studio, monkeypatch)
    counts = adapter(monkeypatch, b"")
    with sessions() as db:
        if change == "config":
            db.get(ModelChannel, cid).revision += 1
        else:
            db.delete(
                db.scalar(
                    select(Membership).where(
                        Membership.project_id == db.get(GenerationJob, jid).project_id,
                        Membership.user_id == ids["user"],
                    )
                )
            )
        db.commit()
        assert receive(db, ids, rid, tid, jid).status == "failed"
        assert db.get(GenerationJob, jid).status == "failed"
    assert counts == {"submit": 0, "poll": []}


@pytest.mark.parametrize("mode", ["routed", "personal"])
@pytest.mark.parametrize("late_status", ["running", "failed", "succeeded"])
def test_old_worker_cannot_cancel_overwrite_or_charge_new_receipt_owner(
    studio, changing_video, monkeypatch, mode, late_status
):
    sessions, ids, rid, tid, jid, _ = failed_job(studio, mode)
    monkeypatch.setattr(delivery, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "celery_eager", False)
    monkeypatch.setattr(delivery, "heartbeat", lambda *a: lambda: None)
    counts, newest = {"submit": 0, "poll": [], "cancel": []}, {}

    class InterleavedVideo:
        def capabilities(self):
            return Capabilities(modalities={"video"})

        def submit(self, req):
            counts["submit"] += 1
            pytest.fail("Lease recovery must not create another upstream job")

        def cancel(self, handle):
            counts["cancel"].append(handle.external_job_id)

        def poll(self, handle):
            counts["poll"].append(handle.external_job_id)
            if len(counts["poll"]) == 1:
                # Interleave two actual worker sessions around an external call.
                with sessions() as check:
                    row = check.get(EngineeringDelivery, jid)
                    assert row and row.lease_token
                    newest["old_token"] = row.lease_token
                    row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=2)
                    check.commit()
                assert delivery.recover_expired() == 1
                with sessions() as recover:
                    resumed = receive(recover, ids, rid, tid, jid)
                    assert resumed.status == "succeeded" and resumed.data["status"] == "pending", (
                        resumed.model_dump()
                    )
                with sessions() as replacement:
                    jobs.execute(replacement, jid)
                    job = replacement.get(GenerationJob, jid)
                    assert job.status == "succeeded", job.error
                    newest["routing_state"] = copy.deepcopy(job.routing_state)
                    newest["token"] = replacement.get(EngineeringDelivery, jid).lease_token
                if late_status == "failed":
                    return GenerationResult(status="failed", error="late old-worker failure")
                if late_status == "running":
                    return GenerationResult(status="running")
            return GenerationResult(
                status="succeeded", outputs=[Output(type="video", data=changing_video)]
            )

    monkeypatch.setattr(generation_registry, "create", lambda *a, **kw: InterleavedVideo())
    with sessions() as db:
        queued = receive(db, ids, rid, tid, jid)
        assert queued.status == "succeeded" and queued.data["status"] == "pending", (
            queued.model_dump()
        )
    with sessions() as old_worker:
        jobs.execute(old_worker, jid)
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        assert job.status == "succeeded" and job.error is None
        assert job.routing_state == newest["routing_state"], (
            "Old provider phase write escaped lease fence"
        )
        assert (
            db.get(EngineeringDelivery, jid).lease_token == newest["token"] != newest["old_token"]
        )
        assert db.scalar(select(func.count()).select_from(Generation)) == 1
        assert float(db.scalar(select(Quota)).used_cost) == 7
    assert counts == {"submit": 0, "poll": ["original-remote-123"] * 2, "cancel": []}


def test_canceled_run_keeps_already_accepted_receipt_as_unselected_candidate(
    studio, changing_video, monkeypatch
):
    sessions, ids, rid, tid, jid, _, _ = lost_worker(studio, monkeypatch)
    calls = {"submit": 0, "cancel": 0}

    class LateVideo:
        def capabilities(self):
            return Capabilities(modalities={"video"})

        def submit(self, req):
            calls["submit"] += 1
            pytest.fail("Existing accepted job was resubmitted")

        def cancel(self, handle):
            calls["cancel"] += 1

        def poll(self, handle):
            assert handle.external_job_id == "original-remote-123"
            with sessions() as control:
                command(control, ids, rid, "cancel")
                control.commit()
            return GenerationResult(
                status="succeeded", outputs=[Output(type="video", data=changing_video)]
            )

    monkeypatch.setattr(generation_registry, "create", lambda *a, **kw: LateVideo())
    with sessions() as db:
        result = receive(db, ids, rid, tid, jid)
        assert result.data["status"] == "succeeded", db.get(GenerationJob, jid).error
        assert db.get(ProductionRun, rid).status == db.get(ProductionTask, tid).status == "canceled"
        assert not db.get(ProductionTask, tid).result
        assert not db.scalar(select(Generation)).is_selected
        assert float(db.scalar(select(Quota)).used_cost) == 7
    assert calls == {"submit": 0, "cancel": 0}


def test_real_process_exit_after_durable_accepted_claim_can_resume_in_new_session(
    studio, changing_video, monkeypatch
):
    sessions, ids, rid, tid, jid, _ = failed_job(studio)
    monkeypatch.setattr(delivery, "SessionLocal", sessions)
    schema = sessions.kw["bind"].get_execution_options()["schema_translate_map"][None]
    assert schema.startswith("test_media_") and schema.removeprefix("test_media_").isalnum()
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        job.status = "pending"
        assert delivery.eligible(job)
        delivery.enqueue(db, job)
        db.commit()
    script = """
import os, uuid
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.models.generation import GenerationJob
from app.modules.engineering import delivery
engine = create_engine(os.environ["TEST_DATABASE_URL"]).execution_options(
    schema_translate_map={None: os.environ["RECOVERY_FIXTURE_SCHEMA"]}
)
with Session(engine) as db:
    job = db.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == uuid.UUID(os.environ["RECOVERY_FIXTURE_JOB"]))
        .with_for_update()
    )
    assert job.status == "pending" and job.routing_state["phase"] == "accepted"
    delivery.claim(db, job)
    job.status = "running"
    db.commit()
os._exit(37)
"""
    # The seeded job was remotely accepted before the child worker was started.
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        job.routing_state = {**job.routing_state, "phase": "accepted"}
        db.commit()
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        env={**os.environ, "RECOVERY_FIXTURE_SCHEMA": schema, "RECOVERY_FIXTURE_JOB": str(jid)},
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == 37, process.stderr.decode(errors="replace")
    with sessions() as db:
        row, job = db.get(EngineeringDelivery, jid), db.get(GenerationJob, jid)
        assert job.status == "running" and row.lease_token
        assert job.external_job_id == "original-remote-123"
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=2)
        db.commit()
    assert delivery.recover_expired() == 1
    counts = adapter(monkeypatch, changing_video)
    with sessions() as db:
        result = receive(db, ids, rid, tid, jid)
        assert result.status == "succeeded" and result.data["status"] == "succeeded", (
            result.model_dump()
        )
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert db.scalar(select(func.count()).select_from(Generation)) == 1
        assert float(db.scalar(select(Quota)).used_cost) == 7
    assert counts == {"submit": 0, "poll": ["original-remote-123"]}
