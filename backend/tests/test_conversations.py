"""Conversation isolation, durable history and replaceable execution boundary."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import create_access_token
from app.models.agent import AgentRun
from app.models.conversation import ConversationMessage
from app.models.identity import Membership, User
from app.modules.agent import worker
from app.modules.agent.schemas import ModelAction
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.fixture(autouse=True)
def legacy_conversation_adapter(monkeypatch):
    """Keep exercising persisted legacy AgentRun chats and the opt-out route."""
    monkeypatch.setattr(settings, "production_harness_enabled", False)


def setup(studio, monkeypatch):
    client, sessions, ids = studio
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    base = f"/api/v1/projects/{ids['project']}/conversations"
    c = post_ok(client, base, {})
    return client, sessions, ids, base, f"{base}/{c['id']}"


def message(content="讨论一个雨夜开场", **kw):
    return {"content": content, "mode": "test", "request_key": str(uuid.uuid4()), **kw}


def finish(client, path, result, monkeypatch):
    run_id = client.get(path).json()["messages"][-1]["task_id"]
    monkeypatch.setattr(worker, "propose", lambda c, p: ModelAction(tool="finish", message=result))
    worker.advance(uuid.UUID(run_id))
    return run_id


def test_followup_receives_history_and_output_is_owned_by_app(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    post_ok(client, path + "/messages", message())
    run_id = finish(client, path, "先用街灯下的倒影开场。", monkeypatch)
    # The response is durable without relying on a subsequent browser poll.
    with sessions() as db:
        reply = db.scalar(
            select(ConversationMessage).where(ConversationMessage.execution_id == uuid.UUID(run_id))
        )
        assert reply.content == "先用街灯下的倒影开场。" and reply.status == "completed"
    sent = post_ok(client, path + "/messages", message("按刚才的方向继续，加入脚步声。"))
    second = sent["messages"][-1]["task_id"]
    seen = []

    def model(c, payload):
        seen.append(payload)
        return ModelAction(tool="finish", message="延续倒影镜头，并加入脚步声。")

    monkeypatch.setattr(worker, "propose", model)
    worker.advance(uuid.UUID(second))
    assert seen[0]["goal"] == "按刚才的方向继续，加入脚步声。"
    history = seen[0]["conversation_context"]["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "先用街灯下的倒影开场。"
    assert len(client.get(path).json()["messages"]) == 4
    assert client.get(base).json()[0]["title"] == "讨论一个雨夜开场"


def test_duplicate_send_and_active_turn_guard(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    payload = message()
    first = post_ok(client, path + "/messages", payload)
    again = post_ok(client, path + "/messages", payload)
    assert first["messages"][0]["id"] == again["messages"][0]["id"]
    assert len(again["messages"]) == 2
    assert client.post(path + "/messages", json=message("另一个请求")).status_code == 409
    assert (
        client.post(path + "/messages", json={**payload, "content": "偷偷改掉原消息"}).status_code
        == 409
    )
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_conversations_never_mix_history_even_with_same_request_key(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    payload = message("方案甲")
    first = post_ok(client, path + "/messages", payload)
    other = post_ok(client, base, {"title": "另一个方向"})
    second = post_ok(client, f"{base}/{other['id']}/messages", {**payload, "content": "方案乙"})
    assert first["messages"][-1]["task_id"] != second["messages"][-1]["task_id"]
    with sessions() as db:
        runs = list(db.scalars(select(AgentRun)))
        assert all(r.input_context["messages"] == [] for r in runs)


def test_rename_archive_restore_preserves_messages(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    post_ok(client, path + "/messages", message())
    finish(client, path, "保留的结果", monkeypatch)
    assert client.patch(path, json={"title": "开场探索", "archived": True}).status_code == 200
    assert client.get(base).json() == []
    assert client.get(base + "?archived=true").json()[0]["title"] == "开场探索"
    assert client.post(path + "/messages", json=message()).status_code == 409
    assert client.get(path).json()["messages"][-1]["content"] == "保留的结果"
    assert client.patch(path, json={"archived": False}).status_code == 200
    assert len(post_ok(client, path + "/messages", message("继续"))["messages"]) == 4


def test_other_project_and_other_member_cannot_read_or_change_conversation(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    task_id = post_ok(client, path + "/messages", message())["messages"][-1]["task_id"]
    cid = path.rsplit("/", 1)[1]
    assert client.get(f"/api/v1/projects/{ids['other']}/conversations/{cid}").status_code == 403
    with sessions() as db:
        user = User(
            username="other_member",
            email="other@example.com",
            password_hash="unused",
            display_name="Other",
        )
        db.add(user)
        db.flush()
        db.add(Membership(project_id=uuid.UUID(ids["project"]), user_id=user.id, role="admin"))
        uid = user.id
        db.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(str(uid))}"
    assert client.get(base).json() == []
    assert client.get(path).status_code == 404
    assert client.patch(path, json={"title": "不能修改"}).status_code == 404
    assert client.post(path + "/messages", json=message()).status_code == 404
    assert client.post(base, json={"id": cid}).status_code == 404
    assert client.get(f"/api/v1/projects/{ids['project']}/agent/runs/{task_id}").status_code == 404
    assert client.get(f"/api/v1/projects/{ids['project']}/agent/runs").json() == []


def test_cancel_continue_and_no_reviving_old_task(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    reply = post_ok(client, path + "/messages", message())["messages"][-1]
    control = f"{path}/messages/{reply['id']}/control"
    assert post_ok(client, control, {"action": "pause"})["status"] == "paused"
    assert client.post(path + "/messages", json=message()).status_code == 409
    assert post_ok(client, control, {"action": "resume"})["status"] == "queued"
    assert post_ok(client, control, {"action": "cancel"})["status"] == "cancelled"
    assert len(post_ok(client, path + "/messages", message("换个方向"))["messages"]) == 4
    assert client.post(control, json={"action": "resume"}).status_code == 409


def test_provider_failure_rolls_back_message_and_task(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    response = client.post(
        path + "/messages", json=message(mode="standard", model_provider="missing_model")
    )
    assert response.status_code in (404, 409, 422)
    assert client.get(path).json()["messages"] == []
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_message_pagination_and_explicit_history_bound(studio, monkeypatch):
    from app.modules.conversation.service import history_snapshot

    client, sessions, ids, base, path = setup(studio, monkeypatch)
    cid = uuid.UUID(path.rsplit("/", 1)[1])
    with sessions() as db:
        rows = [
            ConversationMessage(
                conversation_id=cid,
                ordinal=i,
                role="user" if i % 2 else "assistant",
                content=str(i) * 1000,
                request_key=uuid.uuid4(),
                status="completed",
            )
            for i in range(1, 107)
        ]
        db.add_all(rows)
        db.flush()
        history = history_snapshot(rows)
        assert history["history_truncated"] is True
        assert sum(len(m["content"]) for m in history["messages"]) <= 24000
        db.commit()
    page = client.get(path).json()
    assert page["has_older"] and len(page["messages"]) == 100
    earlier = client.get(path + "?before=" + str(page["messages"][0]["ordinal"])).json()
    assert len(earlier["messages"]) == 6 and not earlier["has_older"]


def test_scope_validation_and_empty_input(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    assert client.post(path + "/messages", json=message("   ")).status_code == 422
    assert client.patch(path, json={"title": "   "}).status_code == 422
    assert (
        client.post(
            path + "/messages",
            json=message(scope=[{"target_type": "shot", "target_id": str(uuid.uuid4())}]),
        ).status_code
        == 404
    )
    assert client.get(path).json()["messages"] == []


def test_tools_can_run_without_agent_session(studio):
    from app.core.deps import ProjectContext
    from app.models.identity import Project
    from app.modules.tools.contracts import ToolContext
    from app.modules.tools.registry import execute

    client, sessions, ids = studio
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        ctx = ProjectContext(
            db.get(Project, uuid.UUID(ids["project"])), member, db.get(User, ids["user"])
        )
        result = execute(db, ctx, ToolContext(), "project.overview", {})
        assert result["shots"]["total"] == 1
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_parallel_send_is_idempotent_and_orders_turns(studio, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    client, sessions, ids, base, path = setup(studio, monkeypatch)
    payload = message()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: client.post(path + "/messages", json=payload), range(2)))
    assert [r.status_code for r in replies] == [200, 200]
    assert len(client.get(path).json()["messages"]) == 2
    finish(client, path, "第一轮结束", monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(
                lambda i: client.post(path + "/messages", json=message(f"并发消息{i}")), range(2)
            )
        )
    assert sorted(r.status_code for r in replies) == [200, 409]
    assert [m["ordinal"] for m in client.get(path).json()["messages"]] == [1, 2, 3, 4]


def test_duplicate_request_cannot_change_execution_configuration(studio, monkeypatch):
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    payload = message()
    post_ok(client, path + "/messages", payload)
    assert client.post(path + "/messages", json={**payload, "mode": "standard"}).status_code == 409
    assert (
        client.post(
            path + "/messages",
            json={**payload, "scope": [{"target_type": "shot", "target_id": ids["shot"]}]},
        ).status_code
        == 409
    )
