"""Durable job -> detached real decoder -> fenced technical-delivery receipt."""

# ruff: noqa: F811
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import func, select, text

from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.models.storage import Blob
from app.modules.generation import jobs
from app.modules.harness import runtime, schemas, service, technical_validation, validation_stage
from app.modules.harness.job_wait import poll_waiting
from app.storage import cas
from tests.test_harness_editing_tools import png, sound
from tests.test_harness_job_wait import prepared
from tests.test_harness_runtime import command
from tests.test_harness_technical_validation import video
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    monkeypatch.setattr(technical_validation, "_CACHE", OrderedDict())
    monkeypatch.setattr(
        jobs, "dispatch", lambda *a, **kw: pytest.fail("validation cannot generate")
    )


def completed(studio, data, *, kind="image", task_kind="tool", expected=None, dependent=False):
    sessions, ids, run_id, task_id, job_id, attempt_id = prepared(studio, kind=task_kind)
    with sessions() as db:
        run, task = db.get(ProductionRun, run_id), db.get(ProductionTask, task_id)
        run.policy = schemas.Policy(budget_points=100, effects=["read", "generate"]).model_dump()
        task.attempts = 1
        task.specification = {"output_requirements": expected or {}}
        db.get(ProductionAttempt, attempt_id).status = "succeeded"
        job = db.get(GenerationJob, job_id)
        job.status, job.actual_cost, job.request_type = "succeeded", 10, kind
        mime = {"image": "image/png", "audio": "audio/wav", "video": "video/mp4"}[kind]
        blob = cas.put_bytes(db, data, mime, duration_ms=1000 if kind != "image" else None)
        generation = Generation(
            project_id=job.project_id,
            job_id=job.id,
            target_type=job.target_type,
            target_id=job.target_id,
            provider="mock",
            output_type=kind,
            output_blob_hash=blob.hash,
            cost_points=10,
            params={},
            input_refs={},
        )
        db.add(generation)
        if dependent:
            db.add(
                ProductionTask(
                    run_id=run_id,
                    key="continue_after_validation",
                    goal="根据已验收结果继续创作",
                    kind="agent",
                    status="queued",
                    dependencies=[str(task_id)],
                    specification={},
                    state={},
                    result={},
                    error={},
                )
            )
        db.commit()
    return sessions, ids, run_id, task_id, job_id


def claim(sessions, run_id):
    with sessions() as db:
        ticket = service.claim(db, run_id)
        assert ticket
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert task.state["validation_job_id"]
        assert task.status == "running"
        db.commit()
        return ticket


@pytest.mark.parametrize("kind", ["image", "audio", "video"])
def test_runtime_requires_real_full_decode_before_delivering(studio, tmp_path, kind):
    data = (
        png("red")
        if kind == "image"
        else sound(1)
        if kind == "audio"
        else video(tmp_path / "film.mp4")
    )
    sessions, _, run_id, task_id, job_id = completed(studio, data, kind=kind)
    with sessions() as db:
        poll_waiting(db, db.get(ProductionRun, run_id))
        task = db.get(ProductionTask, task_id)
        assert task.status == "validating_job"
        assert not task.result["data"]["materialized"]
        assert "technical_validated" not in task.result["data"]
        db.commit()
    ticket = claim(sessions, run_id)
    runtime.execute(ticket, sessions)
    with sessions() as db:
        run, task = db.get(ProductionRun, run_id), db.get(ProductionTask, task_id)
        assert task.status == run.status == "succeeded", task.error
        receipt = task.result["data"]
        assert receipt["materialized"] and receipt["technical_validated"]
        assert receipt["unverified"] == ["creative_quality"] and not receipt["adopted"]
        check = receipt["outputs"][0]["technical_validation"]
        assert check["status"] == "passed" and check["coverage"]["full_decode"]
        assert check["coverage"]["hash_bytes"] == len(data)
        assert "creative_quality" in check["unassessed"]
        assert task.state["technical_checks"] == [check]
        assert "validation_job_id" not in task.state and "waiting_job_id" not in task.state
        assert task.state["steps"][-1]["receipt"] == task.result
        assert (
            db.get(ProductionAttempt, uuid.UUID(ticket["attempt_id"])).operation["phase"]
            == "validate"
        )
        job = db.get(GenerationJob, job_id)
        assert float(job.actual_cost) == 10 and job.status == "succeeded"
        assert job.cost_raw[technical_validation.CACHE_KEY][check["blob_hash"]] == check
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert not db.scalar(select(Generation.is_selected).where(Generation.job_id == job_id))


@pytest.mark.parametrize("fault", ["corrupt", "truncated", "wrong_stream", "missing", "duration"])
def test_bad_delivery_fails_without_regenerating_or_charging(studio, tmp_path, fault):
    data = (
        b"not a media file"
        if fault == "corrupt"
        else sound(1)
        if fault == "wrong_stream"
        else video(tmp_path / "film.mp4")
    )
    if fault == "truncated":
        data = data[: int(len(data) * 0.65)]
    expected = {"duration_ms": 4000} if fault == "duration" else None
    sessions, _, run_id, task_id, job_id = completed(
        studio, data, kind="video", expected=expected, dependent=True
    )
    if fault == "missing":
        with sessions() as db:
            row = db.scalar(select(Generation).where(Generation.job_id == job_id))
            db.get(Blob, row.output_blob_hash).storage_uri = str(tmp_path / "missing.mp4")
            db.commit()
    ticket = claim(sessions, run_id)
    runtime.execute(ticket, sessions)
    with sessions() as db:
        task = db.get(ProductionTask, task_id)
        assert task.status == "failed", task.error
        assert task.error["code"] == "TECHNICAL_VALIDATION_FAILED"
        assert task.error["retryable"] is False
        assert not task.result["data"]["technical_validated"]
        check = task.state["technical_checks"][0]
        assert check["status"] == "failed" and not check["technical_ready"]
        child = db.scalar(
            select(ProductionTask).where(ProductionTask.key == "continue_after_validation")
        )
        assert child.status == "blocked" and child.attempts == 0
        assert db.get(ProductionRun, run_id).status == "failed"
        assert service.claim(db, run_id) is None
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert float(db.get(GenerationJob, job_id).actual_cost) == 10


def test_paused_job_is_recorded_but_no_decoder_or_next_agent_is_dispatched(studio, monkeypatch):
    sessions, ids, run_id, task_id, _ = completed(studio, png("blue"), task_kind="agent")
    monkeypatch.setattr(
        validation_stage, "validate_prepared", lambda *a, **kw: pytest.fail("paused")
    )
    with sessions() as db:
        command(db, ids, run_id, "pause")
        db.commit()
        assert service.claim(db, run_id) is None
        task = db.get(ProductionTask, task_id)
        assert task.status == "validating_job" and task.attempts == 1
        assert db.get(ProductionRun, run_id).status == "paused"
        assert task.state["job_receipt"]["data"]["records_ready"]
        db.commit()


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_user_can_control_during_decode_and_late_result_obeys_fence(studio, monkeypatch, action):
    sessions, ids, run_id, task_id, job_id = completed(studio, png("green"), task_kind="agent")
    ticket = claim(sessions, run_id)
    decoded, release = Event(), Event()
    actual = validation_stage.validate_prepared

    def deferred_return(item, **kwargs):
        receipt = actual(item, **kwargs)
        assert receipt["status"] == "passed"
        decoded.set()
        assert release.wait(10), "test controller did not release decoder"
        # A real decoder may finish before it sees cancellation; publication
        # must still recheck the lease after this technically valid late result.
        return receipt

    monkeypatch.setattr(validation_stage, "validate_prepared", deferred_return)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.execute, ticket, sessions)
        try:
            assert decoded.wait(10), "decoder was not invoked"
            with sessions() as db:
                db.execute(text("SET LOCAL lock_timeout = '1500ms'"))
                command(db, ids, run_id, action)
                db.commit()
                assert db.get(ProductionRun, run_id).status == (
                    "paused" if action == "pause" else "canceled"
                )
        finally:
            release.set()
        future.result(timeout=10)
    with sessions() as db:
        task = db.get(ProductionTask, task_id)
        run = db.get(ProductionRun, run_id)
        attempt = db.get(ProductionAttempt, uuid.UUID(ticket["attempt_id"]))
        assert service.claim(db, run_id) is None
        assert task.attempts == 2
        if action == "pause":
            assert run.status == "paused" and task.status == "queued"
            assert task.result["data"]["technical_validated"]
            assert task.state["steps"][-1]["receipt"] == task.result
            assert attempt.status == "succeeded"
        else:
            assert run.status == task.status == "canceled"
            assert attempt.status == "late_result"
            assert attempt.result["technical_checks"][0]["status"] == "passed"
            assert "technical_validated" not in task.result.get("data", {})
            assert not db.get(GenerationJob, job_id).cost_raw
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1


def test_storage_identity_changed_before_publication_is_not_accepted(studio, tmp_path, monkeypatch):
    data = png("purple")
    sessions, _, run_id, task_id, _ = completed(studio, data)
    ticket = claim(sessions, run_id)
    actual = validation_stage.validate_prepared

    def repaired_after_decode(item, **kwargs):
        receipt = actual(item, **kwargs)
        assert receipt["status"] == "passed"
        original = tmp_path / "repaired.png"
        original.write_bytes(data)
        with sessions() as db:
            cas.put_file(db, original, "image/png", force_store=True)
            db.commit()
        return receipt

    monkeypatch.setattr(validation_stage, "validate_prepared", repaired_after_decode)
    runtime.execute(ticket, sessions)
    with sessions() as db:
        task = db.get(ProductionTask, task_id)
        assert task.status == "failed", task.error
        check = task.state["technical_checks"][0]
        assert check["status"] == "not_checked" and check["error"]["code"] == "SOURCE_CHANGED"
        assert not task.result["data"]["technical_validated"]


def test_recovered_validation_reuses_receipt_without_provider_resubmission(studio, monkeypatch):
    sessions, _, run_id, task_id, job_id = completed(studio, png("orange"))
    first = claim(sessions, run_id)
    with sessions() as db:
        prepared_result = runtime.prepare(db, first)
        assert prepared_result == {"validation_job_id": str(job_id)}
        ctx = service.execution_context(db, db.get(ProductionRun, run_id))
        generation = db.scalar(select(Generation).where(Generation.job_id == job_id))
        validation_input = technical_validation.prepare_validation(db, ctx, generation)
        db.commit()
    receipt = technical_validation.validate_prepared(validation_input)
    with sessions() as db:
        ctx = service.execution_context(db, db.get(ProductionRun, run_id))
        technical_validation.record_validation(db, ctx, validation_input, receipt)
        task = db.get(ProductionTask, task_id)
        task.status, task.lease_token, task.lease_expires_at = "uncertain", None, None
        db.commit()
    technical_validation._CACHE.clear()
    monkeypatch.setattr(
        technical_validation, "_validate", lambda *a, **kw: pytest.fail("already validated")
    )
    second = claim(sessions, run_id)
    assert first["attempt_id"] != second["attempt_id"]
    runtime.execute(second, sessions)
    with sessions() as db:
        task = db.get(ProductionTask, task_id)
        assert task.status == "succeeded", task.error
        assert task.state["technical_checks"][0]["cache_hit"]
        assert db.get(ProductionAttempt, uuid.UUID(first["attempt_id"])).status == "reconciled"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
