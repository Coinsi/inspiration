"""Durable scheduling with real PostgreSQL and explicitly simulated providers."""

# ruff: noqa: F811
import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.adapters.generation.mock import MockProvider
from app.core.config import settings
from app.core.errors import Conflict
from app.models.engineering import EngineeringDelivery
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.modules.engineering import delivery
from app.modules.generation import jobs
from app.modules.generation.model_routing import retry_allowed
from app.modules.harness import job_wait, media_tools
from app.modules.tools import capabilities, registry
from app.modules.tools.invocation import invoke
from tests.test_harness_media_tools import args, context, ctx, setup_run
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    media_tools.register_tools()


def queued(studio, monkeypatch):
    _, sessions, ids = studio
    monkeypatch.setattr(settings, "celery_eager", False)
    monkeypatch.setattr(delivery, "SessionLocal", sessions)
    rid, tasks = setup_run(sessions, ids)
    execution = context(rid, tasks[0])
    with sessions() as db:
        result = invoke(db, ctx(db, ids), execution, "media.generate", args(), allow_charges=True)
        assert result.status == "succeeded", result.model_dump()
        assert result.data["status"] == "pending"
        return sessions, ids, rid, tasks[0], uuid.UUID(result.data["job_id"]), execution


def due(sessions, jid):
    with sessions() as db:
        db.get(EngineeringDelivery, jid).next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()


def test_broker_loss_and_duplicate_messages_use_one_claim_and_one_charge(studio, monkeypatch):
    sessions, _, rid, _, jid, _ = queued(studio, monkeypatch)
    with sessions() as db:
        row = db.get(EngineeringDelivery, jid)
        assert row and row.state == "pending"
        before = copy.deepcopy(db.get(ProductionRun, rid).context["budget_reservations"])
        assert len(before) == 1 and next(iter(before.values()))["job_id"] == str(jid)

    def offline(job):
        raise ConnectionError("secret broker address must not persist")

    monkeypatch.setattr(delivery, "publish", offline)
    assert delivery.pump() == 1
    with sessions() as db:
        assert db.get(EngineeringDelivery, jid).last_error == "ConnectionError"
        assert db.get(GenerationJob, jid).status == "pending"
    published = []
    monkeypatch.setattr(delivery, "publish", lambda job: published.append(job.id))
    due(sessions, jid)
    assert delivery.pump() == 1
    due(sessions, jid)  # Broker acknowledged, worker message was lost.
    assert delivery.pump() == 1 and published == [jid, jid]
    entered, finish = threading.Event(), threading.Event()
    original, submitted = MockProvider.submit, []

    def submit(self, request):
        submitted.append(request.prompt)
        entered.set()
        assert finish.wait(10), "test worker was never released"
        return original(self, request)

    monkeypatch.setattr(MockProvider, "submit", submit)

    def execute():
        with sessions() as db:
            jobs.execute(db, jid)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(execute)
        assert entered.wait(10)
        try:
            executor.submit(execute).result(timeout=10)
            assert len(submitted) == 1  # Second message was rejected by conditional claim.
        finally:
            finish.set()
        first.result(timeout=10)
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        assert job.status == "succeeded", job.error
        assert float(job.actual_cost) == 10
        assert db.scalar(select(func.count()).select_from(Generation)) == 1
        assert db.get(ProductionRun, rid).context["budget_reservations"] == before
        row = db.get(EngineeringDelivery, jid)
        assert row.state == "acknowledged" and row.lease_token is None
    assert delivery.pump() == 0


def test_job_budget_and_delivery_have_one_transaction(studio, monkeypatch):
    _, sessions, ids = studio
    rid, tasks = setup_run(sessions, ids)
    request_key, jid = uuid.uuid4(), uuid.uuid4()
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        run.context = {"budget_reservations": {str(request_key): {"job_id": str(jid)}}}
        job = GenerationJob(
            id=jid,
            project_id=run.project_id,
            created_by=run.created_by,
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="remote-fixture",
            status="pending",
            input_snapshot={
                "harness_origin": {
                    "run_id": str(rid),
                    "task_id": str(tasks[0]),
                    "request_key": str(request_key),
                }
            },
        )
        db.add(job)
        db.flush()
        delivery.enqueue(db, job)
        # Neither job nor outbox is visible to a competing scheduler before commit.
        with sessions() as observer:
            assert observer.get(GenerationJob, jid) is None
            assert observer.get(EngineeringDelivery, jid) is None
        db.rollback()
    with sessions() as db:
        assert db.get(GenerationJob, jid) is None
        assert db.get(EngineeringDelivery, jid) is None
        assert not db.get(ProductionRun, rid).context.get("budget_reservations")


@pytest.mark.parametrize("phase", [None, "dispatching", "submission_unknown"])
def test_unknown_remote_loss_stays_uncertain_without_resubmission_or_budget_release(
    studio, monkeypatch, phase
):
    sessions, ids, rid, tid, jid, execution = queued(studio, monkeypatch)
    submitted = []
    monkeypatch.setattr(delivery, "publish", lambda job: submitted.append(job.id))
    with sessions() as db:
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == jid).with_for_update())
        delivery.claim(db, job)
        job.status, job.routing_state = "running", {"phase": phase} if phase else {}
        db.get(EngineeringDelivery, jid).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        task = db.get(ProductionTask, tid)
        task.status, task.state = "waiting_job", {"waiting_job_id": str(jid)}
        key = uuid.UUID(execution.input_context["execution"]["request_key"])
        db.add(
            ProductionAttempt(
                id=key,
                task_id=tid,
                number=1,
                status="interrupted",
                lease_token=uuid.uuid4(),
                operation={"phase": "tool", "tool": "media.generate"},
            )
        )
        before = copy.deepcopy(db.get(ProductionRun, rid).context)
        db.commit()
    assert delivery.recover_expired() == 1
    assert delivery.recover_expired() == 0 and delivery.pump() == 0
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        assert job.status == "failed" and job.external_job_id is None
        assert job.routing_state["phase"] == "submission_unknown"
        assert job.routing_state["harness_delivery"]["recovery"] == "uncertain"
        assert retry_allowed(job) is False
        with pytest.raises(Conflict):
            jobs.retry(db, ctx(db, ids), jid)
        db.rollback()
        run = db.get(ProductionRun, rid)
        job_wait.poll_waiting(db, run)
        task = db.get(ProductionTask, tid)
        assert task.status == "uncertain" and task.error["retryable"] is False
        assert task.error["recovery"] != "resume_receipt"
        assert job_wait.recover_uncertain(db, run) == []
        assert task.status == "uncertain" and run.context == before
        db.commit()
    with sessions() as db:
        jobs.execute(db, jid)  # A late duplicate cannot turn failed into a fresh submit.
        assert db.scalar(select(func.count()).select_from(Generation)) == 0
    assert not submitted


def test_recover_local_leaves_durable_remote_jobs_to_receipt_policy(studio, monkeypatch):
    sessions, _, _, _, jid, _ = queued(studio, monkeypatch)
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        job.cost_raw = {**job.cost_raw, "executor": "local"}
        job.status = "running"
        delivery.claim(db, job)
        db.commit()
    monkeypatch.setattr(
        jobs.pool, "submit", lambda *a: pytest.fail("legacy recovery requeued remote")
    )
    jobs.recover_local()
    with sessions() as db:
        assert db.get(GenerationJob, jid).status == "running"
