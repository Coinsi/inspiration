"""A completed submission is recovered by observing its job, never resubmitting."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import func, select

from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.models.storage import Blob
from app.modules.generation import jobs, media_engine
from app.modules.harness.job_wait import poll_waiting, recover_uncertain
from app.storage import cas
from tests.test_harness_media_tools import setup_run
from tests.test_media_workflow import studio  # noqa: F401


def test_receipt_retry_crash_recovers_original_job_without_dispatch(studio, monkeypatch):
    from datetime import timedelta

    from app.modules.harness import service
    from tests.test_harness_media_tools import context
    from tests.test_harness_recovery_tools import failed_job, receive

    sessions, ids, rid, tid, jid, _ = failed_job(studio)
    dispatches = []
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: dispatches.append(job.id))
    with sessions() as db:
        run, task = db.get(ProductionRun, rid), db.get(ProductionTask, tid)
        task.status = "waiting_job"
        task.state = {"waiting_job_id": str(jid)}
        db.flush()
        poll_waiting(db, run)
        assert task.error["recovery"] == "resume_receipt"
        task.status = "running"
        attempt = ProductionAttempt(
            task_id=tid,
            number=1,
            lease_token=task.lease_token,
            operation={
                "phase": "tool",
                "tool": "media.resume_receipt",
                "arguments": {"job_id": str(jid)},
            },
            status="started",
            result={},
            error={},
        )
        db.add(attempt)
        db.flush()
        execution = context(rid, tid, request_key=str(attempt.id))
        result = receive(db, ids, rid, tid, jid, execution)
        assert result.status == "succeeded", result.error
        assert dispatches == [jid]
        # The dispatch transaction persisted but the worker died before saving
        # the tool receipt. Expire the lease and let the real scheduler reconcile.
        task.lease_expires_at = service.now() - timedelta(seconds=1)
        db.commit()
        assert service.claim(db, rid) is None
        db.commit()
        assert service.claim(db, rid) is None
        db.commit()
        db.refresh(task)
        assert task.status == "waiting_job"
        assert task.state["waiting_job_id"] == str(jid)
        assert attempt.status == "reconciled"
        assert dispatches == [jid]
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1


def prepared(studio, *, status="pending", kind="tool", task_status="waiting_job"):
    _, sessions, ids = studio
    run_id, tasks = setup_run(sessions, ids)
    with sessions() as db:
        task = db.get(ProductionTask, tasks[0])
        attempt = ProductionAttempt(
            task_id=task.id,
            number=1,
            lease_token=task.lease_token,
            operation={
                "phase": "tool",
                "tool": "media.generate",
                "arguments": {"provider": "mock", "prompt": "固定要求", "request_type": "image"},
            },
            status="started",
            result={},
            error={},
        )
        db.add(attempt)
        db.flush()
        job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            created_by=ids["user"],
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            request_type="image",
            status=status,
            estimated_cost=10,
            params={},
            input_snapshot={
                "harness_origin": {
                    "run_id": str(run_id),
                    "task_id": str(task.id),
                    "request_key": str(attempt.id),
                }
            },
        )
        db.add(job)
        db.flush()
        task.status, task.kind = task_status, kind
        task.state = {"waiting_job_id": str(job.id)} if task_status == "waiting_job" else {}
        task.lease_token, task.lease_expires_at = None, None
        job_id, attempt_id = job.id, attempt.id
        db.commit()
    return sessions, ids, run_id, tasks[0], job_id, attempt_id


def complete(db, job_id, *, materialize=True):
    job = db.get(GenerationJob, job_id)
    job.status, job.actual_cost = "succeeded", 10
    if materialize:
        blob = cas.put_bytes(db, media_engine.mock_image(), "image/png")
        db.add(
            Generation(
                project_id=job.project_id,
                job_id=job.id,
                target_type=job.target_type,
                target_id=job.target_id,
                provider="mock",
                output_type="image",
                output_blob_hash=blob.hash,
                params={},
                input_refs={},
                cost_points=10,
            )
        )
    db.flush()


@pytest.mark.parametrize("kind", ["tool", "agent"])
def test_pending_then_materialized_completion_without_duplicate_receipts(studio, kind):
    sessions, _, run_id, task_id, job_id, _ = prepared(studio, kind=kind)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        first = poll_waiting(db, run)
        assert first[0]["kind"] == "task.job_progress"
        assert db.get(ProductionTask, task_id).status == "waiting_job"
        sequence = run.event_sequence
        assert poll_waiting(db, run) == [] and run.event_sequence == sequence
        complete(db, job_id)
        finished = poll_waiting(db, run)
        task = db.get(ProductionTask, task_id)
        assert finished[0]["kind"] == "task.job_completed"
        assert task.status == "validating_job"
        assert task.result["data"]["records_ready"]
        assert task.result["data"]["materialized"] is False
        assert len(task.state["steps"]) == 1
        assert task.state["waiting_job_id"] == task.state["validation_job_id"] == str(job_id)
        assert poll_waiting(db, run) == []
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1


def test_paused_run_accepts_late_receipt_without_resuming(studio, monkeypatch):
    sessions, _, run_id, task_id, job_id, _ = prepared(studio, kind="agent")
    monkeypatch.setattr(jobs, "dispatch", lambda *a: pytest.fail("poll must never dispatch"))
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        run.status = "paused"
        complete(db, job_id)
        assert poll_waiting(db, run)[0]["kind"] == "task.job_completed"
        assert run.status == "paused"
        assert db.get(ProductionTask, task_id).status == "validating_job"


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_failed_job_requires_new_authorization_not_paid_retry(studio, status):
    sessions, _, run_id, task_id, _, _ = prepared(studio, status=status)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        assert poll_waiting(db, run)[0]["kind"] == "task.job_failed"
        task = db.get(ProductionTask, task_id)
        assert task.status == "failed" and task.error["retryable"] is False
        assert "authorizing_new_generation" in task.error["recovery"]
        assert poll_waiting(db, run) == []


def test_success_without_materialized_output_waits_for_same_job_repair(studio):
    sessions, _, run_id, task_id, job_id, _ = prepared(studio)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        complete(db, job_id, materialize=False)
        assert poll_waiting(db, run)[0]["kind"] == "task.output_unavailable"
        task = db.get(ProductionTask, task_id)
        assert task.status == "waiting_job" and task.error["retryable"] is False
        assert poll_waiting(db, run) == []
        complete(db, job_id)
        assert poll_waiting(db, run)[0]["kind"] == "task.job_completed"
        assert task.status == "validating_job" and task.error == {}


def test_lost_submission_receipt_recovers_exact_attempt_without_dispatch(studio, monkeypatch):
    sessions, _, run_id, task_id, job_id, attempt_id = prepared(studio, task_status="uncertain")
    monkeypatch.setattr(jobs, "dispatch", lambda *a: pytest.fail("recovery must never dispatch"))
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        run.status = "paused"
        recovered = recover_uncertain(db, run)
        task = db.get(ProductionTask, task_id)
        assert recovered[0]["resubmitted"] is False
        assert task.status == "waiting_job" and task.state["waiting_job_id"] == str(job_id)
        assert db.get(ProductionAttempt, attempt_id).status == "reconciled"
        assert run.status == "paused" and recover_uncertain(db, run) == []
        complete(db, job_id)
        poll_waiting(db, run)
        assert task.status == "validating_job" and run.status == "paused"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1


def test_missing_or_different_request_key_stays_uncertain(studio):
    sessions, _, run_id, task_id, job_id, _ = prepared(studio, task_status="uncertain")
    with sessions() as db:
        job = db.get(GenerationJob, job_id)
        job.input_snapshot = {
            "harness_origin": {
                "run_id": str(run_id),
                "task_id": str(task_id),
                "request_key": str(uuid.uuid4()),
            }
        }
        db.flush()
        assert recover_uncertain(db, db.get(ProductionRun, run_id)) == []
        assert db.get(ProductionTask, task_id).status == "uncertain"
        db.delete(job)
        db.flush()
        assert recover_uncertain(db, db.get(ProductionRun, run_id)) == []
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_superseded_task_does_not_publish_late_result(studio):
    sessions, _, run_id, task_id, job_id, _ = prepared(studio)
    with sessions() as db:
        task = db.get(ProductionTask, task_id)
        task.status = "superseded"
        complete(db, job_id)
        assert poll_waiting(db, db.get(ProductionRun, run_id)) == []
        assert task.status == "superseded" and not task.result


def test_deleted_physical_output_requires_worker_validation(studio, monkeypatch):
    sessions, _, run_id, task_id, job_id, _ = prepared(studio)
    with sessions() as db:
        complete(db, job_id)
        generation = db.scalar(select(Generation).where(Generation.job_id == job_id))
        blob = db.get(Blob, generation.output_blob_hash)
        blob.storage_uri = "C:/nonexistent-harness-output.png"
        monkeypatch.setattr(
            cas, "open_range", lambda *a, **kw: pytest.fail("polling must not read storage")
        )
        db.flush()
        poll_waiting(db, db.get(ProductionRun, run_id))
        task = db.get(ProductionTask, task_id)
        assert task.status == "validating_job"
        output = task.result["data"]["outputs"][0]
        assert output["checks"] == ["blob_record"]
        assert output["storage_accessible"] is None
        assert task.result["data"]["materialized"] is False


def test_lost_preview_receipt_recovers_only_matching_fixed_candidate(studio):
    from tests.test_engineering_review import draft, preview, ready

    client, sessions, ids, path, tid, url = ready(studio)
    run_id, task_ids = setup_run(sessions, ids)
    change = draft(client, path, tid, url)
    rendered = preview(client, path, change)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        run.work_id = uuid.UUID(path.split("/")[-2])
        task = db.get(ProductionTask, task_ids[0])
        task.status = "uncertain"
        attempt = ProductionAttempt(
            task_id=task.id,
            number=1,
            lease_token=task.lease_token,
            operation={
                "phase": "tool",
                "tool": "engineering.preview",
                "arguments": {
                    "work_id": str(run.work_id),
                    "change_id": change["id"],
                    "fingerprint": "0" * 64,
                },
            },
            result={},
            error={},
        )
        db.add(attempt)
        db.flush()
        assert recover_uncertain(db, run) == []
        attempt.operation = {
            **attempt.operation,
            "arguments": {**attempt.operation["arguments"], "fingerprint": change["fingerprint"]},
        }
        db.flush()
        assert recover_uncertain(db, run)[0]["job_id"] == rendered["preview_job_id"]
        assert poll_waiting(db, run)[0]["kind"] == "task.job_completed"
        assert task.status == "validating_job"
