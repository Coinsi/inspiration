"""Real persisted media phases and read-only restoration; no paid providers."""

# ruff: noqa: F811
import copy
import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.conversation import ConversationMessage
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun
from app.modules.conversation import production
from app.modules.generation import media_engine
from app.modules.harness import kernel, runtime, service
from app.storage import cas
from tests import test_harness_conversation as harness
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_kernel(monkeypatch):
    monkeypatch.setattr(settings, "production_harness_enabled", True)
    monkeypatch.setattr(kernel, "_KERNELS", dict(kernel._KERNELS))
    kernel._KERNELS["mock"] = lambda *_: {"kind": "finish", "message": "离线测试完成"}


def persisted_job(db, ids, *, project=None, status="running"):
    job = GenerationJob(
        project_id=project or uuid.UUID(ids["project"]),
        target_type="shot",
        target_id=uuid.UUID(ids["shot"]),
        provider="mock",
        request_type="image",
        status=status,
        input_snapshot={},
        created_by=ids["user"],
        routing_state={"phase": "accepted", "private_channel_note": "must-not-be-projected"},
    )
    db.add(job)
    db.flush()
    return job


def attach_job(task, job):
    task.status = "waiting_job"
    task.state = {"waiting_job_id": str(job.id)}
    task.result = {"tool": "media.generate", "status": "succeeded", "data": {"job_id": str(job.id)}}


def saved_output(db, ids, job):
    blob = cas.put_bytes(db, media_engine.mock_image(), "image/png", width=512, height=512)
    generation = Generation(
        project_id=job.project_id,
        job_id=job.id,
        target_type="shot",
        target_id=uuid.UUID(ids["shot"]),
        provider="mock",
        output_type="image",
        output_blob_hash=blob.hash,
    )
    db.add(generation)
    db.flush()
    return generation


def test_progress_uses_real_receipt_stages_and_partial_outputs_before_run_finishes(studio):
    _, sessions, ids = studio
    with sessions() as db:
        message, run, _ = harness.start(
            db,
            ids,
            harness.conversation(db, ids),
            mode="standard",
            media={"provider": "mock", "request_type": "image"},
        )
        assert message.context["media_progress"]["phase"] == "queued"
        assert message.content == "收到，我来按这些要求生成图片。"
        task = service.tasks(db, run)[0]
        job = persisted_job(db, ids)
        attach_job(task, job)
        run.status = "running"
        db.flush()
        production.publish(db, run)
        progress = message.context["media_progress"]
        assert progress["phase"] == "generating" and progress["job_id"] == str(job.id)
        assert progress["started_at"] and progress["updated_at"]
        assert "must-not-be-projected" not in str(message.context)
        assert "percent" not in progress and "eta" not in progress
        unchanged = copy.deepcopy(progress)
        production.publish(db, run)
        assert message.context["media_progress"] == unchanged

        job.routing_state = {"phase": "generated"}
        db.flush()
        production.publish(db, run)
        assert message.context["media_progress"]["phase"] == "materializing"
        assert message.context["outputs"] == []

        job.status = "succeeded"
        generation = saved_output(db, ids, job)
        task.status = "validating_job"
        task.state = {**task.state, "validation_job_id": str(job.id)}
        db.flush()
        production.publish(db, run)
        assert message.status == "running"
        assert message.context["media_progress"]["phase"] == "materializing"
        assert message.context["media_progress"]["completed_outputs"] == 1
        assert [output["id"] for output in message.context["outputs"]] == [str(generation.id)]
        task.status = "succeeded"
        run.status = "succeeded"
        db.flush()
        production.publish(db, run)
        assert message.context["media_progress"]["phase"] == "completed"


def test_progress_ignores_cross_project_jobs_and_superseded_tasks(studio):
    _, sessions, ids = studio
    with sessions() as db:
        message, run, _ = harness.start(
            db,
            ids,
            harness.conversation(db, ids),
            mode="standard",
            media={"provider": "mock", "request_type": "image"},
        )
        task = service.tasks(db, run)[0]
        job = persisted_job(db, ids, project=ids["other"])
        attach_job(task, job)
        db.flush()
        production.publish(db, run)
        assert "job_id" not in message.context["media_progress"]
        assert message.context["outputs"] == []
        task.status = "superseded"
        db.flush()
        production.publish(db, run)
        assert message.context["media_progress"] is None


def test_reply_archives_old_progress_and_tracks_the_same_submitted_job(studio):
    client, sessions, ids, path = harness.api_conversation(studio)
    created = post_ok(
        client,
        path + "/messages",
        {
            "request_key": str(uuid.uuid4()),
            "content": "一只蓝色杯子",
            "mode": "standard",
            "media": {"provider": "mock", "request_type": "image"},
        },
    )
    initial = created["messages"][-1]
    run_id = uuid.UUID(initial["task_id"])
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        job = persisted_job(db, ids)
        attach_job(service.tasks(db, run)[0], job)
        run.status = "running"
        db.commit()
        job_id = str(job.id)
    current = client.get(path).json()["messages"][-1]
    assert current["context"]["media_progress"]["phase"] == "generating"
    guidance = {
        "request_key": str(uuid.uuid4()),
        "content": "保持杯子的蓝色",
        "mode": "standard",
        "production": {
            "run_id": str(run_id),
            "action": "respond",
            "expected_revision": current["context"]["production"]["revision"],
        },
    }
    detail = post_ok(client, path + "/messages", guidance)
    archived, latest = detail["messages"][1], detail["messages"][-1]
    assert archived["status"] == "completed"
    assert archived["context"]["production"]["is_current"] is False
    assert archived["context"]["media_progress"] is None
    assert archived["context"]["media"] == initial["context"]["media"]
    assert latest["context"]["production"]["is_current"] is True
    assert latest["context"]["media_progress"]["job_id"] == job_id
    assert latest["context"]["media_progress"]["phase"] == "generating"
    assert len(post_ok(client, path + "/messages", guidance)["messages"]) == 4
    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(job_id))
        job.routing_state = {"phase": "generated"}
        db.commit()
    updated = client.get(path).json()
    assert updated["messages"][1] == archived
    assert updated["messages"][-1]["context"]["media_progress"]["phase"] == "materializing"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert db.scalar(select(func.count()).select_from(ProductionRun)) == 1


def test_completed_production_restore_is_read_only_and_keeps_frozen_inputs(studio):
    client, sessions, ids, path = harness.api_conversation(studio)
    payload = {
        "request_key": str(uuid.uuid4()),
        "content": "一只蓝色杯子",
        "mode": "standard",
        "media": {"provider": "mock", "request_type": "image", "provider_params": {}},
    }
    created = post_ok(client, path + "/messages", payload)
    reply = created["messages"][-1]
    endpoint = f"{path}/messages/{reply['id']}"
    assert client.get(endpoint + "/retry-input").status_code == 409
    run_id = uuid.UUID(reply["task_id"])
    for _ in range(2):
        with sessions() as db:
            ticket = service.claim(db, run_id)
            assert ticket
            db.commit()
        runtime.execute(ticket, sessions)
    assert client.get(path).json()["messages"][-1]["status"] == "completed"
    with sessions() as db:
        source = db.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == uuid.UUID(created["id"]),
                ConversationMessage.role == "user",
            )
        )
        # Restore preserves the original reference/attachment identities and
        # pinned skill versions; it does not resolve or silently replace them.
        original = copy.deepcopy(source.context)
        original["skills"] = [{"id": str(uuid.uuid4()), "revision": 3}]
        original["attachment_requests"] = [
            {"attachment_id": str(uuid.uuid4()), "role": "reference"}
        ]
        source.context = original
        db.commit()
        before = (
            db.scalar(select(func.count()).select_from(GenerationJob)),
            db.scalar(select(func.count()).select_from(ProductionRun)),
            db.get(ProductionRun, run_id).revision,
        )
    restored = client.get(endpoint + "/retry-input")
    assert restored.status_code == 200, restored.text
    value = restored.json()
    assert value["content"] == payload["content"]
    assert value["media"] == original["media"]
    assert value["skills"] == original["skills"]
    assert value["attachments"] == original["attachment_requests"]
    assert client.get(endpoint + "/retry-input").json() == value
    assert (
        client.post(endpoint + "/retry", json={"request_key": str(uuid.uuid4())}).status_code == 409
    )
    other = post_ok(client, f"/api/v1/projects/{ids['project']}/conversations", {})
    foreign = (
        f"/api/v1/projects/{ids['project']}/conversations/{other['id']}/messages/{reply['id']}"
    )
    assert client.get(foreign + "/retry-input").status_code == 404
    with sessions() as db:
        assert before == (
            db.scalar(select(func.count()).select_from(GenerationJob)),
            db.scalar(select(func.count()).select_from(ProductionRun)),
            db.get(ProductionRun, run_id).revision,
        )


def test_completed_legacy_restore_does_not_enable_retry_execution(studio, monkeypatch):
    from tests.test_conversation_media import media_message
    from tests.test_conversations import setup

    monkeypatch.setattr(settings, "production_harness_enabled", False)
    client, sessions, _, _, path = setup(studio, monkeypatch)
    payload = media_message()
    reply = post_ok(client, path + "/messages", payload)["messages"][-1]
    endpoint = f"{path}/messages/{reply['id']}"
    assert reply["status"] == "completed"
    assert reply["context"]["media_progress"]["phase"] == "completed"
    restored = client.get(endpoint + "/retry-input")
    assert restored.status_code == 200, restored.text
    assert restored.json()["content"] == payload["content"]
    assert (
        client.post(endpoint + "/retry", json={"request_key": str(uuid.uuid4())}).status_code == 409
    )
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
