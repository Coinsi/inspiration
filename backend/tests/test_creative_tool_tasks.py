"""Tool execution, durable previews, idempotency and stale adoption boundaries."""

# ruff: noqa: F811
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.adapters.assist.mock import MockAssist
from app.adapters.decomposition.mock import MockDecomposer
from app.models.narrative import Scene, Script
from app.modules.assist import service as assist
from app.modules.narrative import service, tool_tasks
from tests.test_media_workflow import post_ok, studio  # noqa: F401


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


def test_legacy_scene_progress_in_editor_and_task_center(studio, monkeypatch):
    from app.models.narrative import CreativeToolTask

    client, sessions, ids, root, script = setup_tools(studio, monkeypatch)
    jobs = root + "/creative-tool-tasks"
    data = {
        "operation": "script_decompose",
        "target_id": script["id"],
        "request_key": str(uuid.uuid4()),
    }
    task_id = client.post(jobs, json=data).json()["id"]
    scenes = [
        {"title": "街道", "shots": [{"title": "镜头", "still_prompt": "private prompt"}]},
        {"title": "街道", "error": "模型请求失败", "shots": []},
    ]
    with sessions.begin() as db:
        task = db.get(CreativeToolTask, task_id)
        task.status = "succeeded"
        task.result = {"output": {"scenes": scenes, "strategy": "mock"}}
    row = client.get(root + "/activity?category=story&operation=script_decompose").json()["items"][
        0
    ]
    assert row["status"] == "partial" and row["target_name"] == script["title"]
    assert row["scene_progress"]["shots"] == row["scene_progress"]["failed"] == 1
    assert "private prompt" not in str(row)
    url = root + f"/activity/creative/{task_id}"
    detail = client.get(url).json()
    assert [s["state"] for s in detail["scenes"]] == ["succeeded", "failed"]
    assert detail["actions"][0]["path"].endswith("/retry-scenes")
    assert client.get(
        f"/api/v1/projects/{ids['other']}/activity/creative/{task_id}"
    ).status_code in (403, 404)
    editor = client.get(
        jobs, params={"operation": data["operation"], "target_id": script["id"]}
    ).json()
    assert editor["result"]["progress"]["completed"] == row["scene_progress"]["completed"] == 1
    with sessions.begin() as db:
        task = db.get(CreativeToolTask, task_id)
        assert "progress" not in task.result
        task.result = {"output": {"scenes": scenes[1:], "strategy": "mock"}}
    assert client.get(url).json()["summary"]["status"] == "failed"
    assert client.get(root + "/activity?category=media").json()["total"] == 0


def test_retry_saves_a_long_storyboard_streamed_one_character_at_a_time(studio, monkeypatch):
    from app.adapters.decomposition.cloud_llm import CloudLLMDecomposer
    from tests.test_litellm_sdk import upstream

    client, _, _, root, script = setup_tools(studio, monkeypatch)
    blocks = [
        {"block_type": "scene_heading", "text": "街道"},
        {"block_type": "action", "text": "街灯亮起"},
        {"block_type": "scene_heading", "text": "房间"},
        {"block_type": "action", "text": "推开木门"},
    ]
    assert client.put(root + f"/scripts/{script['id']}/blocks", json={"blocks": blocks}).is_success
    shots = [
        {
            "title": f"镜头 {i + 1}",
            "still_prompt": "夕阳穿过窗户洒进房间，窗帘下的木地板映出淡淡暖光。" * 21,
            "motion_prompt": "镜头从门旁缓慢向前，光线随窗帘的起伏而变化。" * 12,
        }
        for i in range(12)
    ]
    long_reply = json.dumps({"shots": shots}, ensure_ascii=False)
    calls = []

    def frame(content, reason=None):
        data = {
            "id": "chatcmpl-regression",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-6.2-sol",
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": reason}],
        }
        return b"data: " + json.dumps(data, ensure_ascii=False).encode() + b"\n\n"

    def reply(_method, _path, body):
        messages = json.loads(body)["messages"]
        if "资产管理助手" in messages[0]["content"]:
            return 200, "text/event-stream", frame('{"entities":[]}', "stop") + b"data: [DONE]\n\n"
        scene = messages[-1]["content"]
        calls.append(scene)
        if scene.startswith("街灯亮起"):
            data = frame(json.dumps({"shots": shots[:1]}, ensure_ascii=False), "stop")
        elif calls.count(scene) == 1:
            data = frame('{"shots":[', "length")
        else:
            data = b"".join(frame(char) for char in long_reply) + frame("", "stop")
        return 200, "text/event-stream", data + b"data: [DONE]\n\n"

    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "127.0.0.1,localhost")
    with upstream(reply) as (endpoint, _):
        strategy = CloudLLMDecomposer(endpoint, "test-key", "gpt-6.2-sol")
        monkeypatch.setattr(service, "_strategy", lambda *_: strategy)
        jobs = root + "/creative-tool-tasks"
        params = {"operation": "script_decompose", "target_id": script["id"]}
        task = client.post(jobs, json={**params, "request_key": str(uuid.uuid4())}).json()
        assert tool_tasks.run_once()
        detail_url = root + f"/activity/creative/{task['id']}"
        first = client.get(detail_url).json()
        assert first["summary"]["status"] == "partial"
        assert "长度上限" in first["scenes"][1]["error"]
        saved = client.get(jobs, params=params).json()["result"]["output"]["scenes"][0]

        assert client.post(jobs + f"/{task['id']}/retry-scenes").is_success
        assert tool_tasks.run_once()
        result = client.get(jobs, params=params).json()["result"]
        kept, retried = result["output"]["scenes"]
        assert kept == saved
        assert [s["title"] for s in retried["shots"]] == [s["title"] for s in shots]
        assert [s["still_prompt"] for s in retried["shots"]] == [s["still_prompt"] for s in shots]
        assert [s["motion_prompt"] for s in retried["shots"]] == [s["motion_prompt"] for s in shots]
        assert result["progress"]["completed"] == 2 and result["progress"]["failed"] == 0
        assert client.get(detail_url).json()["summary"]["status"] == "succeeded"
        assert len(calls) == 3 and calls[0].startswith("街灯亮起")
        assert calls[1] == calls[2]
        assert "上场结尾（仅供接戏）\n街道\n街灯亮起\n\n本场剧本\n推开木门" in calls[1]


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


def test_scene_adoption_fills_empty_existing_scenes_and_preserves_history(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    script = post_ok(client, root + "/scripts", {"title": "场次补入镜头"})
    path = root + f"/scripts/{script['id']}/apply-scenes"
    street = {"title": "街口", "body": "风起"}
    night = {"title": "街口", "body": "入夜"}
    room = {"title": "房间", "body": "有人推门"}
    old, kept, legacy = post_ok(
        client,
        path,
        {
            "scenes": [
                {**street, "shots": [{"title": "旧镜头"}]},
                {**night, "shots": [{"title": "已编辑的镜头"}]},
                room,
            ]
        },
    )
    with sessions() as db:
        db.get(Scene, uuid.UUID(legacy["id"])).source_key = None
        db.commit()

    def shot_path(scene):
        return root + f"/scenes/{scene['id']}/shots"

    old_shot = client.get(shot_path(old)).json()[0]
    original = client.get(shot_path(kept)).json()[0]
    assert client.delete(root + f"/shots/{old_shot['id']}").is_success
    drafts = {
        "scenes": [
            {**street, "shots": [{"title": "开场"}, {"title": "远景"}]},
            {**night, "shots": [{"title": "另一种构图"}]},
            {**room, "shots": [{"title": "进屋"}]},
            {"title": "未完成", "error": "模型没有返回镜头"},
        ]
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: post_ok(client, path, drafts), range(2)))
    added = client.get(shot_path(old)).json()
    assert [s["title"] for s in added] == ["开场", "远景"]
    assert old_shot["id"] not in {s["id"] for s in added}
    assert [s["id"] for s in client.get(shot_path(old) + "/removed").json()] == [old_shot["id"]]
    assert [s["id"] for s in client.get(shot_path(kept)).json()] == [original["id"]]
    assert [s["title"] for s in client.get(shot_path(legacy)).json()] == ["进屋"]
    assert sorted(sum(row["shots_added"] for row in reply) for reply in replies) == [0, 3]
    for reply in replies:
        assert [row["id"] for row in reply] == [old["id"], kept["id"], legacy["id"]]
        assert not any(row["scene_created"] for row in reply)
        assert reply[1]["shots_kept"] == 1
    stats = client.get(root + f"/scripts/{script['id']}/stats").json()
    assert (stats["scenes"], stats["shots"]) == (3, 4)
    insertion = {"title": "另一处街口", "body": "天亮前"}
    extra, unchanged = post_ok(client, path, {"scenes": [insertion, street]})
    assert unchanged["id"] == old["id"] and extra["ordinal"] > max(r["ordinal"] for r in replies[0])


def test_reopen_applied_suggestions_and_explicitly_refill_a_cleared_scene(studio, monkeypatch):
    client, sessions, _, root, script = setup_tools(studio, monkeypatch)
    jobs = root + "/creative-tool-tasks"
    request = {
        "operation": "script_decompose",
        "target_id": script["id"],
        "request_key": str(uuid.uuid4()),
    }
    task = client.post(jobs, json=request).json()
    assert tool_tasks.run_once()
    apply = jobs + f"/{task['id']}/apply"
    applied = post_ok(client, apply, {})
    scene = client.get(root + f"/scripts/{script['id']}/scenes").json()[0]
    shots = root + f"/scenes/{scene['id']}/shots"
    originals = {s["id"] for s in client.get(shots).json()}
    assert originals
    post_ok(client, shots + "/remove", {"shot_ids": list(originals)})
    # A delayed replay of the original adoption must respect the removal.
    post_ok(client, apply, {})
    assert not client.get(shots).json()
    reopened = client.post(jobs, json={**request, "request_key": str(uuid.uuid4())}).json()
    assert (reopened["id"], reopened["status"]) == (task["id"], "applied")
    refilled = post_ok(client, apply, {"fill_empty_scenes": True})
    current = {s["id"] for s in client.get(shots).json()}
    assert len(current) == len(originals) and current.isdisjoint(originals)
    assert {s["id"] for s in client.get(shots + "/removed").json()} == originals
    # Saving the script already created the scene. Both adoptions reuse its identity.
    assert not applied["scenes"][0]["scene_created"] and not refilled["scenes"][0]["scene_created"]
    assert refilled["scenes"][0]["shots_added"] == len(current)
    assert post_ok(client, apply, {"fill_empty_scenes": True})["scenes"][0]["shots_added"] == 0
    with sessions() as db:
        db.get(Script, script["id"]).title = "新版本"
        db.commit()
    assert client.post(apply, json={"fill_empty_scenes": True}).status_code == 409


def test_scene_breakdown_does_not_reuse_a_receipt_for_removed_shots(studio, monkeypatch):
    client, _, ids, root, _ = setup_tools(studio, monkeypatch)
    scene_id = client.get(root + f"/shots/{ids['shot']}").json()["scene_id"]
    shots = root + f"/scenes/{scene_id}/shots"
    jobs = root + "/creative-tool-tasks"

    def submit():
        response = client.post(
            jobs,
            json={
                "operation": "scene_breakdown",
                "target_id": scene_id,
                "request_key": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 202
        return response.json()

    first = submit()
    assert tool_tasks.run_once()
    assert submit()["id"] == first["id"]
    original_ids = {row["id"] for row in client.get(shots).json()}
    post_ok(client, shots + "/remove", {"shot_ids": list(original_ids)})
    next_task = submit()
    assert next_task["id"] != first["id"] and next_task["status"] == "queued"
    assert submit()["id"] == next_task["id"]
    assert tool_tasks.run_once()
    current = {row["id"] for row in client.get(shots).json()}
    assert current and current.isdisjoint(original_ids)
    assert {row["id"] for row in client.get(shots + "/removed").json()} == original_ids


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
        if operation == "scene_breakdown":
            url = root + f"/activity/creative/{restored['id']}"
            detail = client.get(url).json()
            progress = detail["summary"]["scene_progress"]
            assert progress["unit"] == "shot"
            assert progress["total"] == progress["completed"] == len(detail["scenes"]) > 0
            assert progress["failed"] == 0
            # Existing receipts have only the saved shots. Mixed outcomes must remain visible.
            from app.models.narrative import CreativeToolTask
            with sessions.begin() as db:
                entry = db.get(CreativeToolTask, restored["id"])
                entry.result = {"output": [
                    {"title": "街角", "storyboard": {"still_prompt": "private visual description"}},
                    {"title": "路口", "storyboard": {"breakdown_error": "模型没有返回分镜"}},
                ]}
            older = client.get(url).json()
            assert older["summary"]["status"] == "partial"
            assert older["summary"]["scene_progress"]["failed"] == 1
            assert older["scenes"][1]["error"] == "模型没有返回分镜"
            assert "private visual description" not in str(older)
        assert (
            client.post(root + "/creative-tool-tasks", json=request).json()["id"]
            == response.json()["id"]
        )
