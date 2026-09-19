"""Bounded execution, review conflicts, scope checks and durable tool feedback."""

# ruff: noqa: F811
import uuid

from sqlalchemy import select

from app.models.agent import AgentRun
from app.models.generation import Generation, GenerationJob
from app.models.shot import Shot
from app.modules.agent import worker
from app.modules.agent.schemas import ModelAction
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def setup(studio, monkeypatch, scope=True, max_turns=8):
    client, sessions, ids = studio
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    root = f"/api/v1/projects/{ids['project']}/agent/runs"
    request = {
        "goal": "把镜头改为金色海岸",
        "engine": "mock",
        "scope": [{"target_type": "shot", "target_id": ids["shot"]}] if scope else [],
        "max_turns": max_turns,
        "request_key": str(uuid.uuid4()),
    }
    run = post_ok(client, root, request)
    assert post_ok(client, root, request)["id"] == run["id"]
    return client, sessions, ids, root + "/" + run["id"], run


def tick(run, n=1):
    for _ in range(n):
        worker.advance(uuid.UUID(run["id"]))


def test_review_apply_feedback_and_duplicate_approval(studio, monkeypatch):
    client, sessions, ids, path, run = setup(studio, monkeypatch)

    def model(config, payload):
        if not payload["steps"]:
            return ModelAction(tool="object.read", arguments=payload["scope"][0])
        if len(payload["steps"]) == 1:
            return ModelAction(
                tool="object.propose_edit",
                arguments={
                    **payload["scope"][0],
                    "summary": "统一海岸名称",
                    "ops": [{"op": "set_field", "field": "title", "value": "金色海岸"}],
                },
            )
        assert payload["steps"][-1]["applied"]["state"]["fields"]["title"] == "金色海岸"
        return ModelAction(tool="finish", message="镜头名称已更新")

    monkeypatch.setattr(worker, "propose", model)
    tick(run, 4)
    state = client.get(path).json()
    assert state["status"] == "waiting_review"
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "Test shot"
    decision = f"{path}/steps/{state['steps'][-1]['id']}/decision"
    post_ok(client, decision, {"action": "approve"})
    post_ok(client, decision, {"action": "approve"})
    assert client.post(decision, json={"action": "reject"}).status_code == 409
    tick(run)
    assert client.get(path).json()["status"] == "succeeded"
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "金色海岸"


def test_stale_proposal_rejected_and_scope_cannot_expand(studio, monkeypatch):
    client, sessions, ids, path, run = setup(studio, monkeypatch)
    monkeypatch.setattr(
        worker,
        "propose",
        lambda c, p: ModelAction(
            tool="object.propose_edit",
            arguments={
                **p["scope"][0],
                "summary": "改名",
                "ops": [{"op": "set_field", "field": "title", "value": "建议名字"}],
            },
        ),
    )
    tick(run, 2)
    state = client.get(path).json()
    step = state["steps"][0]
    with sessions() as db:
        db.get(Shot, uuid.UUID(ids["shot"])).title = "其他成员刚修改的名字"
        db.commit()
    assert (
        client.post(f"{path}/steps/{step['id']}/decision", json={"action": "approve"}).status_code
        == 409
    )
    post_ok(
        client, f"{path}/steps/{step['id']}/decision", {"action": "reject", "note": "保留新名字"}
    )
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "其他成员刚修改的名字"
    client, sessions, ids, path2, run2 = setup(studio, monkeypatch, False)
    monkeypatch.setattr(
        worker,
        "propose",
        lambda c, p: ModelAction(
            tool="object.propose_edit",
            arguments={
                "target_type": "shot",
                "target_id": ids["shot"],
                "summary": "越过范围",
                "ops": [{"op": "set_field", "field": "title", "value": "不能改"}],
            },
        ),
    )
    tick(run2, 2)
    result = client.get(path2).json()
    assert result["steps"][0]["status"] == "failed"
    assert "授权" in result["steps"][0]["result"]["error"]


def test_interrupted_model_cancel_and_bounded_loop(studio, monkeypatch):
    client, sessions, ids, path, run = setup(studio, monkeypatch, max_turns=1)
    monkeypatch.setattr(
        worker,
        "propose",
        lambda c, p: ModelAction(tool="unknown.shell", arguments={"command": "delete all"}),
    )
    tick(run, 3)
    state = client.get(path).json()
    assert state["status"] == "paused" and state["turns"] == 1
    assert client.post(path + "/control", json={"action": "resume"}).status_code == 409
    post_ok(client, path + "/control", {"action": "resume", "additional_turns": 2})
    with sessions() as db:
        r = db.get(AgentRun, uuid.UUID(run["id"]))
        r.status = "thinking"
        r.claim_id = uuid.uuid4()
        db.commit()
    tick(run)
    assert client.get(path).json()["status"] == "paused"
    post_ok(client, path + "/control", {"action": "cancel"})
    tick(run, 2)
    assert client.get(path).json()["status"] == "canceled"


def test_generation_approval_job_result_feedback(studio, monkeypatch):
    client, sessions, ids, path, run = setup(studio, monkeypatch)

    def model(config, payload):
        if not payload["steps"]:
            return ModelAction(
                tool="generation.request",
                arguments={
                    **payload["scope"][0],
                    "provider": "mock",
                    "prompt": "海边镜头",
                    "count": 2,
                },
            )
        assert payload["steps"][0]["execution"]["status"] == "succeeded"
        assert len(payload["steps"][0]["execution"]["outputs"]) == 2
        return ModelAction(tool="finish", message="两张候选已保存，尚未采用")

    monkeypatch.setattr(worker, "propose", model)
    tick(run, 2)
    state = client.get(path).json()
    assert state["status"] == "waiting_review"
    with sessions() as db:
        assert not list(db.scalars(select(GenerationJob)))
    decision = f"{path}/steps/{state['steps'][0]['id']}/decision"
    post_ok(client, decision, {"action": "approve"})
    post_ok(client, decision, {"action": "approve"})
    tick(run, 2)
    assert client.get(path).json()["status"] == "succeeded"
    with sessions() as db:
        assert len(list(db.scalars(select(GenerationJob)))) == 1
        assert all(
            g.input_refs["agent_origin"]["run_id"] == run["id"]
            for g in db.scalars(select(Generation))
        )
        assert db.get(Shot, uuid.UUID(ids["shot"])).selected_generation_id is None


def test_model_result_discarded_after_cancel(studio, monkeypatch):
    client, sessions, ids, path, run = setup(studio, monkeypatch)

    def cancel_during_model(config, payload):
        post_ok(client, path + "/control", {"action": "cancel"})
        return ModelAction(
            tool="object.propose_edit",
            arguments={
                **payload["scope"][0],
                "summary": "迟到提议",
                "ops": [{"op": "set_field", "field": "title", "value": "不应该出现"}],
            },
        )

    monkeypatch.setattr(worker, "propose", cancel_during_model)
    tick(run)
    result = client.get(path).json()
    assert result["status"] == "canceled" and result["steps"] == []


def test_compatible_response_never_finishes_before_tool_result():
    import pytest
    from app.core.errors import AppError
    read='{"tool":"object.read","arguments":{"target_type":"shot","target_id":"x"},"message":"读取"}'
    finish='{"tool":"finish","arguments":{},"message":"已读取"}'
    assert worker.parse_response(read+finish).tool=="object.read"
    assert worker.parse_response(read+read).tool=="object.read"
    with pytest.raises(AppError):worker.parse_response(finish+read)
    with pytest.raises(AppError):worker.parse_response(read+'{"tool":"generation.request","arguments":{},"message":""}')
