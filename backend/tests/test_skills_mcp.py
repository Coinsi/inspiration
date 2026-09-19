"""Immutable skill versions, external review boundary and source import atomicity."""

# ruff: noqa: F811
import uuid

from sqlalchemy import select

from app.models.agent import AgentRun
from app.models.asset import Asset
from app.models.identity import Membership
from app.models.shot import Shot
from app.modules.agent import worker
from app.modules.agent.schemas import ModelAction
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_skill_snapshot_and_archive_do_not_change_existing_runs(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    body = {
        "name": "连续性检查",
        "description": "测试方法",
        "instructions": "读取对象再提出最小修改",
        "required_tools": ["object.read"],
        "source": "测试模板",
    }
    skill = post_ok(client, root + "/skills", body)
    assert (
        client.get(root + "/skills/" + skill["id"]).json()["instructions"] == body["instructions"]
    )
    assert client.get(f"/api/v1/projects/{ids['other']}/skills/{skill['id']}").status_code == 403
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="owner"))
        db.commit()
    assert client.get(f"/api/v1/projects/{ids['other']}/skills/{skill['id']}").status_code == 404
    run = post_ok(
        client,
        root + "/agent/runs",
        {
            "goal": "检查镜头",
            "engine": "mock",
            "skills": [skill["id"]],
            "request_key": str(uuid.uuid4()),
        },
    )
    assert (
        client.put(
            root + "/skills/" + skill["id"],
            json={**body, "revision": 1, "instructions": "后来修改的方法"},
        ).status_code
        == 200
    )
    assert (
        client.put(root + "/skills/" + skill["id"], json={**body, "revision": 1}).status_code == 409
    )
    post_ok(client, root + "/skills/" + skill["id"] + "/archive", {"revision": 2, "archived": True})
    monkeypatch.setattr(
        worker,
        "propose",
        lambda c, p: ModelAction(
            tool="skill.load", arguments={"id": p["available_skills"][0]["id"]}
        ),
    )
    worker.advance(uuid.UUID(run["id"]))
    worker.advance(uuid.UUID(run["id"]))
    state = client.get(root + "/agent/runs/" + run["id"]).json()
    assert state["steps"][0]["result"]["instructions"] == body["instructions"]
    assert state["steps"][0]["result"]["revision"] == 1
    assert (
        client.post(
            root + "/agent/runs",
            json={
                "goal": "新任务",
                "engine": "mock",
                "skills": [skill["id"]],
                "request_key": str(uuid.uuid4()),
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            root + "/skills", json={**body, "required_tools": ["shell.execute"]}
        ).status_code
        == 422
    )
    restored = post_ok(
        client, root + "/skills/" + skill["id"] + "/history/1/restore", {"revision": 3}
    )
    assert restored["revision"] == 4 and restored["instructions"] == body["instructions"]


def test_external_calls_read_or_stage_review_never_apply_directly(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    path = root + "/agent/tool-invocations"
    read = post_ok(client, path, {"tool": "project.overview", "arguments": {}})
    assert read["kind"] == "result" and read["result"]["shots"]["total"] == 1
    data = {
        "tool": "object.propose_edit",
        "arguments": {
            "target_type": "shot",
            "target_id": ids["shot"],
            "summary": "外部改名",
            "ops": [{"op": "set_field", "field": "title", "value": "来自外部的提议"}],
        },
        "request_key": str(uuid.uuid4()),
    }
    result = post_ok(client, path, data)
    run = result["run"]
    step = run["steps"][0]
    assert result["kind"] == "review" and run["status"] == "waiting_review"
    assert post_ok(client, path, data)["run"]["id"] == run["id"]
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "Test shot"
    decision = f"{root}/agent/runs/{run['id']}/steps/{step['id']}/decision"
    applied = post_ok(client, decision, {"action": "approve"})
    assert applied["status"] == "succeeded" and applied["engine"] == "external"
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(
        worker,
        "propose",
        lambda *_: (_ for _ in ()).throw(AssertionError("External run must never call model")),
    )
    assert not worker.advance(uuid.UUID(run["id"]))
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "来自外部的提议"
    assert (
        client.post(
            path, json={"tool": "object.read", "arguments": {"target_id": "invalid"}}
        ).status_code
        == 422
    )
    assert client.post(path, json={"tool": "shell.execute", "arguments": {}}).status_code == 422
    with sessions() as db:
        db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        ).role = "viewer"
        db.commit()
    assert client.post(path, json={"tool": "project.overview", "arguments": {}}).status_code == 200
    assert client.post(path, json={**data, "request_key": str(uuid.uuid4())}).status_code == 403


def test_source_catalogue_validation_and_idempotence(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    data = {
        "request_key": str(uuid.uuid4()),
        "source": "只读外部目录",
        "items": [
            {
                "type": "location",
                "name": "海边木屋",
                "summary": "面向大海",
                "source_id": "abc",
                "source_url": "https://example.com/item/abc",
                "tags": ["海岸"],
            }
        ],
    }
    imported = post_ok(client, root + "/source-catalogues/import", data)
    assert (
        post_ok(client, root + "/source-catalogues/import", data)["asset_ids"]
        == imported["asset_ids"]
    )
    assert (
        client.post(
            root + "/source-catalogues/import", json={**data, "source": "改变内容但复用编号"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            root + "/source-catalogues/import",
            json={
                **data,
                "request_key": str(uuid.uuid4()),
                "items": [data["items"][0], {"name": "非法项", "type": "unknown"}],
            },
        ).status_code
        == 422
    )
    with sessions() as db:
        assets = list(db.scalars(select(Asset)))
        assert len(assets) == 1
        assert assets[0].meta["external_source"]["source_id"] == "abc"
        assert assets[0].current_version_id


def test_locked_object_cannot_be_changed_by_old_or_new_assistant(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    with sessions() as db:
        db.get(Shot, uuid.UUID(ids["shot"])).status = "locked"
        db.commit()
    data = {
        "tool": "object.propose_edit",
        "arguments": {
            "target_type": "shot",
            "target_id": ids["shot"],
            "summary": "禁止改名",
            "ops": [{"op": "set_field", "field": "title", "value": "不要应用"}],
        },
        "request_key": str(uuid.uuid4()),
    }
    assert client.post(root + "/agent/tool-invocations", json=data).status_code == 423
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "Test shot"
        assert not list(db.scalars(select(AgentRun)))
