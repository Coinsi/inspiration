"""One durable conversation across text and media; no paid provider calls."""

# ruff: noqa: F811
import uuid

from sqlalchemy import func, select

from app.models.agent import AgentRun
from app.models.generation import GenerationJob
from app.models.identity import Membership
from app.modules.generation import jobs
from tests.test_conversations import finish, message, setup
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def media_message(kind="image", **kwargs):
    return message(
        "一只蓝色陶瓷杯",
        mode="standard",
        media={
            "provider": "mock",
            "request_type": kind,
            **kwargs,
        },
    )


def test_text_image_video_share_history_and_persist_results(studio, monkeypatch):
    from app.adapters.generation.mock import MockProvider

    original_caps = MockProvider.capabilities

    def reference_caps(self):
        caps = original_caps(self)
        caps.features.add("reference")
        caps.max_reference_images = 1
        return caps

    monkeypatch.setattr(MockProvider, "capabilities", reference_caps)
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    post_ok(client, path + "/messages", message("先讨论杯子广告"))
    finish(client, path, "蓝色杯子，白色桌面。", monkeypatch)
    payload = media_message()
    first = post_ok(client, path + "/messages", payload)
    reply = first["messages"][-1]
    assert reply["execution_kind"] == "generation"
    assert reply["status"] == "completed", reply
    output = reply["context"]["outputs"][0]
    assert output["type"] == "image" and output["blob_hash"]
    again = post_ok(client, path + "/messages", payload)
    assert len(again["messages"]) == 4
    assert again["messages"][-1]["task_id"] == reply["task_id"]
    assert (
        client.post(
            path + "/messages",
            json={**payload, "media": {**payload["media"], "provider_params": {"duration": 4}}},
        ).status_code
        == 409
    )
    video = post_ok(
        client,
        path + "/messages",
        media_message("video", references=[{"generation_id": output["id"], "role": "reference"}]),
    )
    assert video["messages"][-1]["status"] == "completed", video
    assert video["messages"][-1]["context"]["outputs"][0]["type"] == "video"
    follow = post_ok(client, path + "/messages", message("继续讨论刚才的结果"))
    with sessions() as db:
        run = db.get(AgentRun, uuid.UUID(follow["messages"][-1]["task_id"]))
        history = run.input_context["messages"]
        assert history[3]["media_outputs"][0]["id"] == output["id"]
        assert "不能声称已看过" in history[3]["media_note"]
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 2
    assert len(client.get(path).json()["messages"]) == 8
    assert (
        client.get(
            f"/api/v1/projects/{ids['project']}/library/generated/{output['id']}"
        ).status_code
        == 200
    )


def test_media_failure_and_cancel_do_not_block_followup(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    sent = post_ok(client, path + "/messages", media_message())
    reply = sent["messages"][-1]
    assert reply["status"] == "queued"
    assert client.post(path + "/messages", json=message()).status_code == 409
    endpoint = f"{path}/messages/{reply['id']}/control"
    assert client.post(endpoint, json={"action": "pause"}).status_code == 422
    assert post_ok(client, endpoint, {"action": "cancel"})["status"] == "cancelled"
    second = post_ok(client, path + "/messages", media_message())
    with sessions() as db:
        j = db.get(GenerationJob, uuid.UUID(second["messages"][-1]["task_id"]))
        j.status = "failed"
        j.error = "供应商测试错误"
        db.commit()
    # No browser poll required before sending the next turn.
    next_turn = post_ok(client, path + "/messages", message("讨论另一种方案"))
    assert next_turn["messages"][3]["error"] == "供应商测试错误"


def test_media_ownership_permissions_and_invalid_input(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    bad = media_message(provider="missing-provider")
    assert client.post(path + "/messages", json=bad).status_code in (404, 422, 409)
    assert client.get(path).json()["messages"] == []
    assert (
        client.post(
            path + "/messages",
            json={**media_message(), "scope": [{"target_type": "shot", "target_id": ids["shot"]}]},
        ).status_code
        == 422
    )
    with sessions() as db:
        m = db.scalar(select(Membership).where(Membership.project_id == uuid.UUID(ids["project"])))
        m.role = "viewer"
        db.commit()
    assert client.post(path + "/messages", json=media_message()).status_code == 403


def test_same_key_in_two_conversations_creates_separate_media_tasks(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    payload = media_message()
    a = post_ok(client, path + "/messages", payload)
    other = post_ok(client, base, {})
    b = post_ok(client, f"{base}/{other['id']}/messages", payload)
    assert a["messages"][-1]["task_id"] != b["messages"][-1]["task_id"]


def test_retry_keeps_original_request_and_is_idempotent(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    payload = media_message("video", provider_params={"duration": 3})
    first = post_ok(client, path + "/messages", payload)["messages"][-1]
    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(first["task_id"]))
        job.status = "failed"
        job.error = "test upstream unavailable"
        db.commit()
    retry_path = f"{path}/messages/{first['id']}"
    original = client.get(retry_path + "/retry-input").json()
    assert original["content"] == payload["content"]
    assert original["media"]["provider_params"] == {"duration": 3}
    assert original["media"]["provider"] == "mock"
    key = {"request_key": str(uuid.uuid4())}
    result = post_ok(client, retry_path + "/retry", key)
    assert len(result["messages"]) == 4
    assert result["messages"][1]["status"] == "failed"
    assert result["messages"][-1]["task_id"] != first["task_id"]
    duplicate = post_ok(client, retry_path + "/retry", key)
    assert len(duplicate["messages"]) == 4
    assert duplicate["messages"][-1]["task_id"] == result["messages"][-1]["task_id"]
    assert (
        client.post(retry_path + "/retry", json={"request_key": str(uuid.uuid4())}).status_code
        == 409
    )
    assert (
        client.get(f"{path}/messages/{result['messages'][-1]['id']}/retry-input").status_code == 409
    )
    post_ok(client, f"{path}/messages/{result['messages'][-1]['id']}/control", {"action": "cancel"})
    client.patch(path, json={"archived": True})
    assert (
        client.post(retry_path + "/retry", json={"request_key": str(uuid.uuid4())}).status_code
        == 409
    )
    other = post_ok(client, base, {})
    assert client.get(f"{base}/{other['id']}/messages/{first['id']}/retry-input").status_code == 404
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 2


def test_retry_agent_preserves_context_without_resuming_old_run(studio, monkeypatch):
    from app.models.conversation import ConversationMessage

    client, sessions, ids, base, path = setup(studio, monkeypatch)
    first = post_ok(client, path + "/messages", message("独立的重试验收"))["messages"][-1]
    post_ok(client, f"{path}/messages/{first['id']}/control", {"action": "cancel"})
    result = post_ok(
        client, f"{path}/messages/{first['id']}/retry", {"request_key": str(uuid.uuid4())}
    )
    assert result["messages"][-1]["task_id"] != first["task_id"]
    assert result["messages"][-2]["content"] == "独立的重试验收"
    assert result["messages"][-1]["context"]["mode"] == "test"
    with sessions() as db:
        old = db.get(ConversationMessage, uuid.UUID(first["id"]))
        assert old.status == "cancelled"
