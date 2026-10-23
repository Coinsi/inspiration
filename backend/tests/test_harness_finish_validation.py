"""A coordinator revalidates selected final outputs using its own requirements."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import text

from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.modules.harness import runtime, schemas, service, technical_validation, validation_stage
from tests.test_harness_context import context
from tests.test_harness_evidence import complete, nested, produced
from tests.test_harness_runtime import command, registered_fixture_kernel  # noqa: F401
from tests.test_media_workflow import changing_video, studio  # noqa: F401


def queue_finish(studio, changing_video, *, duration=2000):
    sessions, ids, rid, parent, children, _ = nested(
        studio,
        requirements={"duration_ms": duration, "duration_tolerance_ms": 100, "has_video": True},
    )
    with sessions() as db:
        ctx = context(db, ids)
        _, image_receipt = produced(db, ctx, ids)
        video, video_receipt = produced(db, ctx, ids, raw=changing_video)
        complete(db.get(ProductionTask, children[0]), image_receipt)
        complete(db.get(ProductionTask, children[1]), video_receipt)
        service.reconcile(db, db.get(ProductionRun, rid))
        db.commit()
        ticket = service.claim(db, rid)
        db.commit()
        runtime.apply_action(
            db,
            ticket,
            schemas.FinishAction(kind="finish", message="交付最终影片", artifacts=[video]),
        )
        db.commit()
        task = db.get(ProductionTask, parent)
        assert task.status == "validating_job"
        assert task.state["validation_finish"]["action"]["artifacts"] == [video]
        assert not task.result.get("verified_outputs")
    return sessions, ids, rid, parent, video


def claim(sessions, rid):
    with sessions() as db:
        ticket = service.claim(db, rid)
        assert ticket
        db.commit()
        return ticket


def test_parent_checks_own_requirements_then_replays_original_fixed_finish(studio, changing_video):
    sessions, ids, rid, parent, video = queue_finish(studio, changing_video)
    runtime.execute(claim(sessions, rid), sessions)
    with sessions() as db:
        task = db.get(ProductionTask, parent)
        assert task.status == "queued", task.error
        assert task.state["pending_action"]["artifacts"] == [video]
        check = task.state["finish_technical_checks"][0]
        assert check["status"] == "passed" and check["expected"]["duration_tolerance_ms"] == 100
        # Another legitimate consumer overwrites the single job-level cache.
        # The parent's persisted receipt must still survive a process restart.
        technical_validation.validate_output(db, context(db, ids), video, expected={})
        db.commit()
    technical_validation._CACHE.clear()
    runtime.execute(claim(sessions, rid), sessions)
    with sessions() as db:
        task = db.get(ProductionTask, parent)
        assert task.status == "succeeded", task.error
        assert task.result["message"] == "交付最终影片"
        assert task.result["verified_outputs"][0]["reference"] == video
        assert (
            task.result["verified_outputs"][0]["technical_validation"]["expected"][
                "duration_tolerance_ms"
            ]
            == 100
        )


def test_actual_parent_requirement_mismatch_fails_without_regeneration(studio, changing_video):
    sessions, _, rid, parent, _ = queue_finish(studio, changing_video, duration=4000)
    runtime.execute(claim(sessions, rid), sessions)
    with sessions() as db:
        task = db.get(ProductionTask, parent)
        assert task.status == "failed" and task.error["code"] == "TECHNICAL_VALIDATION_FAILED"
        assert task.error["retryable"] is False
        assert task.state["finish_technical_checks"][0]["error"]["code"] == "EXPECTATION_MISMATCH"
        assert "pending_action" not in task.state


def test_crashed_validation_attempt_resumes_fixed_selection(studio, changing_video):
    sessions, _, rid, parent, video = queue_finish(studio, changing_video)
    first = claim(sessions, rid)
    with sessions() as db:
        assert runtime.prepare(db, first) == {"validation_finish": True}
        task = db.get(ProductionTask, parent)
        task.status, task.lease_token, task.lease_expires_at = "uncertain", None, None
        db.commit()
    recovered = claim(sessions, rid)
    assert recovered["attempt_id"] != first["attempt_id"]
    runtime.execute(recovered, sessions)
    runtime.execute(claim(sessions, rid), sessions)
    with sessions() as db:
        assert db.get(ProductionAttempt, uuid.UUID(first["attempt_id"])).status == "reconciled"
        assert db.get(ProductionTask, parent).result["verified_outputs"][0]["reference"] == video


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_parent_decode_does_not_hold_run_lock_and_obeys_control_fence(
    studio, changing_video, monkeypatch, action
):
    sessions, ids, rid, parent, _ = queue_finish(studio, changing_video)
    ticket = claim(sessions, rid)
    ready, release = Event(), Event()
    actual = validation_stage.validate_prepared

    def slow(item, **kwargs):
        check = actual(item, **kwargs)
        ready.set()
        assert release.wait(10)
        return check

    monkeypatch.setattr(validation_stage, "validate_prepared", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.execute, ticket, sessions)
        try:
            assert ready.wait(10)
            with sessions() as db:
                db.execute(text("SET LOCAL lock_timeout = '1500ms'"))
                command(db, ids, rid, action)
                db.commit()
        finally:
            release.set()
        future.result(timeout=10)
    with sessions() as db:
        run, task = db.get(ProductionRun, rid), db.get(ProductionTask, parent)
        assert service.claim(db, rid) is None
        if action == "pause":
            assert run.status == "paused" and task.status == "queued"
            assert task.state["pending_action"]["kind"] == "finish"
        else:
            assert run.status == task.status == "canceled"
            assert (
                db.get(ProductionAttempt, uuid.UUID(ticket["attempt_id"])).status == "late_result"
            )
            assert not task.result.get("verified_outputs")


def test_finish_still_requires_creative_report_after_technical_revalidation(studio, changing_video):
    sessions, _, rid, parent, _ = queue_finish(studio, changing_video)
    with sessions() as db:
        task = db.get(ProductionTask, parent)
        task.specification = {
            **task.specification,
            "quality_requirements": {
                "criteria": [{"id": "visual", "instruction": "画面满足要求", "kind": "visual"}]
            },
        }
        db.commit()
    runtime.execute(claim(sessions, rid), sessions)
    runtime.execute(claim(sessions, rid), sessions)
    with sessions() as db:
        task = db.get(ProductionTask, parent)
        assert task.status == "failed"
        assert task.state["finish_technical_checks"][0]["status"] == "passed"
        assert not task.result.get("verified_outputs")
