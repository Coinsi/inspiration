"""Tool execution, durable previews, idempotency and stale adoption boundaries."""

# ruff: noqa: F811
import uuid
from sqlalchemy import select, func
from app.models.narrative import Script, Scene
from app.modules.narrative import tool_tasks, service
from app.modules.assist import service as assist
from app.adapters.decomposition.mock import MockDecomposer
from app.adapters.assist.mock import MockAssist
from tests.test_media_workflow import studio, post_ok  # noqa: F401


def setup_tools(studio, monkeypatch):
    client, sessions, ids = studio
    monkeypatch.setattr(tool_tasks, "SessionLocal", sessions)
    monkeypatch.setattr(service, "_strategy", lambda *args: MockDecomposer())
    monkeypatch.setattr(assist, "_engine", lambda *args: MockAssist())
    root = f"/api/v1/projects/{ids['project']}"
    script = post_ok(client, root + "/scripts", {"title": "Morning"})
    client.put(
        root + f"/scripts/{script['id']}/blocks",
        json={
            "blocks": [
                {"block_type": "scene_heading", "text": "内景 房间 清晨"},
                {"block_type": "action", "text": "少年推开门。"},
            ]
        },
    )
    return client, sessions, ids, root, script


def test_tool_dedup_and_atomic_adoption(studio, monkeypatch):
    client, sessions, ids, root, script = setup_tools(studio, monkeypatch)
    payload = {
        "operation": "script_decompose",
        "target_id": script["id"],
        "request_key": str(uuid.uuid4()),
    }
    first = client.post(root + "/creative-tool-tasks", json=payload)
    assert first.status_code == 202, first.text
    task = first.json()
    assert client.post(root + "/creative-tool-tasks", json=payload).json()["id"] == task["id"]
    assert (
        client.post(
            root + "/creative-tool-tasks", json={**payload, "request_key": str(uuid.uuid4())}
        ).json()["id"]
        == task["id"]
    )
    assert tool_tasks.run_once()
    restored = client.get(
        root + "/creative-tool-tasks",
        params={"operation": payload["operation"], "target_id": script["id"]},
    ).json()
    assert restored["status"] == "succeeded" and restored["result"]["output"]["scenes"]
    url = root + f"/creative-tool-tasks/{task['id']}/apply"
    assert client.post(url, json={}).status_code == 200
    with sessions() as db:
        count = db.scalar(
            select(func.count()).select_from(Scene).where(Scene.script_id == script["id"])
        )
    assert client.post(url, json={}).status_code == 200
    with sessions() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Scene).where(Scene.script_id == script["id"])
            )
            == count
        )


def test_stale_preview_and_foreign_target(studio, monkeypatch):
    client, sessions, ids, root, script = setup_tools(studio, monkeypatch)
    payload = {
        "operation": "script_decompose",
        "target_id": script["id"],
        "request_key": str(uuid.uuid4()),
    }
    task = client.post(root + "/creative-tool-tasks", json=payload).json()
    assert tool_tasks.run_once()
    with sessions() as db:
        db.get(Script, script["id"]).title = "Changed"
        db.commit()
    assert (
        client.post(root + f"/creative-tool-tasks/{task['id']}/apply", json={}).status_code == 409
    )
    assert (
        client.post(
            root + "/creative-tool-tasks", json={**payload, "target_id": str(uuid.uuid4())}
        ).status_code
        == 404
    )


def test_assistant_restores_message_and_rejects_stale_proposal(studio, monkeypatch):
    client, sessions, ids, root, script = setup_tools(studio, monkeypatch)
    chat = post_ok(
        client,
        root + "/assist/chats?reuse=true",
        {"target_type": "script", "target_id": script["id"]},
    )
    assert (
        post_ok(
            client,
            root + "/assist/chats?reuse=true",
            {"target_type": "script", "target_id": script["id"]},
        )["id"]
        == chat["id"]
    )
    payload = {
        "operation": "assist_message",
        "target_id": chat["id"],
        "request_key": str(uuid.uuid4()),
        "content": "标题：新标题",
    }
    response = client.post(root + "/creative-tool-tasks", json=payload)
    assert response.status_code == 202, response.text
    assert tool_tasks.run_once()
    restored = client.get(root + f"/assist/chats/{chat['id']}").json()
    assert len(restored["messages"]) == 2
    message = restored["messages"][-1]
    with sessions() as db:
        db.get(Script, script["id"]).title = "User edit"
        db.commit()
    assert (
        client.post(root + f"/assist/chats/{chat['id']}/messages/{message['id']}/apply").status_code
        == 409
    )
    assert (
        client.post(root + "/creative-tool-tasks", json=payload).json()["id"]
        == response.json()["id"]
    )


def test_breakdown_and_entity_tools(studio, monkeypatch):
    client, sessions, ids, root, script = setup_tools(studio, monkeypatch)
    from app.models.shot import Shot

    with sessions() as db:
        scene_id = db.get(Shot, ids["shot"]).scene_id
    for operation, target in [("scene_breakdown", scene_id), ("script_entities", script["id"])]:
        request = {
            "operation": operation,
            "target_id": str(target),
            "request_key": str(uuid.uuid4()),
        }
        response = client.post(root + "/creative-tool-tasks", json=request)
        assert response.status_code == 202, response.text
        assert tool_tasks.run_once()
        restored = client.get(
            root + "/creative-tool-tasks", params={"operation": operation, "target_id": str(target)}
        ).json()
        assert restored["status"] == "succeeded", restored
        assert isinstance(restored["result"]["output"], list)
        assert (
            client.post(root + "/creative-tool-tasks", json=request).json()["id"]
            == response.json()["id"]
        )
