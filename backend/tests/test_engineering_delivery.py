# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.engineering import EngineeringDelivery
from app.models.generation import Generation, GenerationJob
from app.modules.engineering import delivery
from app.modules.generation import jobs
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_delivery_survives_broker_failure_and_lost_ack_without_duplicate_render(
    studio, monkeypatch
):
    client, sessions, _, path, _, _ = ready(studio)
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": str(uuid.uuid4()),
                    "expected_revision": 0,
                    "kind": "image.document",
                    "title": "durable image",
                    "inputs": {},
                    "payload": {"canvas": {"width": 16, "height": 16}, "layers": []},
                }
            ],
        },
    )
    monkeypatch.setattr(settings, "celery_eager", False)
    monkeypatch.setattr(delivery, "SessionLocal", sessions)
    queued = post_ok(
        client, path + f"/changes/{change['id']}/preview", {"fingerprint": change["fingerprint"]}
    )
    identity = uuid.UUID(queued["preview_job_id"])

    def fail(job):
        raise ConnectionError("sensitive broker address must not persist")

    monkeypatch.setattr(delivery, "publish", fail)
    assert delivery.pump() == 1
    with sessions() as db:
        row = db.get(EngineeringDelivery, identity)
        assert row.attempts == 1 and row.state == "pending"
        assert row.last_error == "ConnectionError"
        assert db.get(GenerationJob, identity).status == "pending"
        assert row.next_attempt_at > datetime.now(UTC)
    sent = []
    monkeypatch.setattr(delivery, "publish", lambda job: sent.append(job.id))

    def due():
        with sessions() as db:
            db.get(EngineeringDelivery, identity).next_attempt_at = datetime.now(UTC) - timedelta(
                seconds=1
            )
            db.commit()

    due()
    # A concurrent dispatcher skips a record another dispatcher currently holds.
    with sessions() as db:
        db.scalar(
            select(EngineeringDelivery)
            .where(EngineeringDelivery.job_id == identity)
            .with_for_update()
        )
        assert delivery.pump() == 0
        db.rollback()
    assert delivery.pump() == 1
    due()  # Published message was lost before worker claim: resend the same job identity.
    assert delivery.pump() == 1
    assert sent == [identity, identity]
    with sessions() as db:
        jobs.execute(db, identity)
        jobs.execute(db, identity)
        assert (
            db.scalar(
                select(func.count()).select_from(Generation).where(Generation.job_id == identity)
            )
            == 1
        )
        assert db.get(GenerationJob, identity).status == "succeeded"
    due()
    assert delivery.pump() == 0  # Claim is the durable acknowledgement.
    with sessions() as db:
        assert db.get(EngineeringDelivery, identity).state == "acknowledged"
    assert len(sent) == 2


def test_job_and_delivery_rollback_together_and_external_jobs_are_excluded(studio):
    _, sessions, ids = studio
    with sessions() as db:
        job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="local",
            status="pending",
            input_snapshot={
                "operation": "engineering_render",
                "engineering_change_id": str(uuid.uuid4()),
            },
        )
        db.add(job)
        db.flush()
        identity = job.id
        delivery.enqueue(db, job)
        db.rollback()
    with sessions() as db:
        assert db.get(EngineeringDelivery, identity) is None
        assert db.get(GenerationJob, identity) is None
        external = GenerationJob(
            provider="external",
            input_snapshot={"operation": "engineering_render", "engineering_change_id": "x"},
        )
        assert not delivery.eligible(external)
        with pytest.raises(ValueError):
            delivery.enqueue(db, external)


@pytest.mark.parametrize("late_failure", [False, True])
def test_expired_worker_cannot_publish_or_fail_recovered_job(studio, monkeypatch, late_failure):
    from contextlib import contextmanager

    from app.modules.engineering import executors

    client, sessions, _, path, _, _ = ready(studio)
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": str(uuid.uuid4()),
                    "expected_revision": 0,
                    "kind": "image.document",
                    "title": "recover",
                    "inputs": {},
                    "payload": {"canvas": {"width": 16, "height": 16}, "layers": []},
                }
            ],
        },
    )
    monkeypatch.setattr(settings, "celery_eager", False)
    monkeypatch.setattr(delivery, "SessionLocal", sessions)
    queued = post_ok(
        client, path + f"/changes/{change['id']}/preview", {"fingerprint": change["fingerprint"]}
    )
    identity = uuid.UUID(queued["preview_job_id"])
    original_execute = executors.execute
    calls = []

    @contextmanager
    def interrupted(plan, read_blob, disk_blob, canceled):
        calls.append(plan["fingerprint"])
        if len(calls) == 1:
            with sessions() as check:
                row = check.get(EngineeringDelivery, identity)
                old_token = row.lease_token
                row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                check.commit()
            assert delivery.recover_expired() == 1
            with sessions() as replacement:
                jobs.execute(replacement, identity)
            with sessions() as check:
                assert not delivery.owned(check, identity, old_token)
                assert check.get(GenerationJob, identity).status == "succeeded"
            if late_failure:
                raise RuntimeError("late stale worker failure")
        # Deliberately ignore cancellation: publication must still be fenced.
        with original_execute(plan, read_blob, disk_blob, lambda: False) as result:
            yield result

    monkeypatch.setattr(executors, "execute", interrupted)
    with sessions() as db:
        jobs.execute(db, identity)
    with sessions() as db:
        job = db.get(GenerationJob, identity)
        assert job.status == "succeeded" and not job.error
        assert (
            db.scalar(
                select(func.count()).select_from(Generation).where(Generation.job_id == identity)
            )
            == 1
        )
    assert len(calls) == 2 and calls[0] == calls[1]
    assert delivery.recover_expired() == 0
