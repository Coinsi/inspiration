"""Canvas transactions, tenant permissions, dependencies and restart reconciliation."""

# ruff: noqa: F811
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.adapters.generation.mock import MockProvider
from app.models.canvas import CanvasRun
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership
from app.models.shot import Shot
from app.modules.canvas import worker
from app.modules.generation import jobs
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def node(key, kind, shot=None):
    return {
        "id": key,
        "position": {"x": 0, "y": 0},
        "data": {
            "kind": kind,
            "label": key,
            "text": "日落海边的电影镜头",
            "target_type": "shot",
            "target_id": shot,
            "generate": {"provider": "mock", "request_type": "image", "count": 1},
        },
    }


def setup(client, ids):
    base = f"/api/v1/projects/{ids['project']}/canvases"
    canvas = post_ok(client, base, {"name": "测试画布"})
    path = f"{base}/{canvas['id']}"
    document = {
        "nodes": [
            node("idea", "text"),
            node("a", "generate", ids["shot"]),
            node("b", "generate", ids["shot"]),
        ],
        "edges": [
            {
                "id": "e1",
                "source": "idea",
                "target": "a",
                "sourceHandle": "text",
                "targetHandle": "prompt",
            },
            {
                "id": "e2",
                "source": "a",
                "target": "b",
                "sourceHandle": "text",
                "targetHandle": "prompt",
            },
        ],
    }
    result = client.put(path, json={"name": "测试画布", "revision": 0, "document": document})
    assert result.status_code == 200, result.text
    return path, result.json()


def tick(sessions, run_id, n=1):
    for _ in range(n):
        with sessions() as db:
            worker.advance(db, uuid.UUID(run_id))


def test_graph_history_validation_permissions(studio):
    client, sessions, ids = studio
    path, doc = setup(client, ids)
    save = {"name": doc["name"], "revision": 1, "document": doc["document"]}
    cycle = copy.deepcopy(save)
    cycle["document"]["edges"].append(
        {
            "id": "cycle",
            "source": "b",
            "target": "a",
            "sourceHandle": "text",
            "targetHandle": "prompt",
        }
    )
    assert client.put(path, json=cycle).status_code == 422
    bad = copy.deepcopy(save)
    bad["document"]["edges"][0]["targetHandle"] = "reference"
    assert client.put(path, json=bad).status_code == 422
    bad = copy.deepcopy(save)
    bad["document"]["nodes"][1]["data"]["target_id"] = str(uuid.uuid4())
    assert client.put(path, json=bad).status_code == 404
    save["name"] = "新名字"
    assert client.put(path, json=save).status_code == 200
    assert client.put(path, json=save).status_code == 409
    restored = post_ok(client, path + "/history/1/restore", {"revision": 2})
    assert restored["name"] == "测试画布" and restored["revision"] == 3
    assert client.get(path.replace(ids["project"], str(ids["other"]))).status_code == 403
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert client.get(path).status_code == 200
    assert client.put(path, json={**save, "revision": 3}).status_code == 403
    assert (
        client.post(
            path + "/runs", json={"revision": 3, "request_key": str(uuid.uuid4())}
        ).status_code
        == 403
    )


def test_execution_pause_restart_frozen_inputs_and_idempotence(studio):
    client, sessions, ids = studio
    path, doc = setup(client, ids)
    request = {"revision": 1, "request_key": str(uuid.uuid4())}
    run = post_ok(client, path + "/runs", request)
    assert post_ok(client, path + "/runs", request)["id"] == run["id"]
    assert (
        client.post(path + "/runs", json={**request, "request_key": str(uuid.uuid4())}).status_code
        == 409
    )
    control = path + f"/runs/{run['id']}/control"
    tick(
        sessions, run["id"], 2
    )  # text completes, first generation dispatched and completed by eager executor
    post_ok(client, control, {"action": "pause"})
    tick(sessions, run["id"], 4)
    paused = client.get(path + "/runs").json()[0]
    assert paused["status"] == "paused" and paused["steps"]["b"]["status"] == "waiting"
    # Editing a later revision never changes inputs of an existing batch.
    newer = copy.deepcopy(doc["document"])
    newer["nodes"][0]["data"]["text"] = "不得混入当前批次"
    assert (
        client.put(path, json={"name": "新版本", "revision": 1, "document": newer}).status_code
        == 200
    )
    post_ok(client, control, {"action": "resume"})
    tick(sessions, run["id"], 8)  # new DB sessions simulate scheduler restarts
    result = client.get(path + "/runs").json()[0]
    assert result["status"] == "succeeded", result
    tick(sessions, run["id"], 3)
    with sessions() as db:
        all_jobs = list(db.scalars(select(GenerationJob)))
        assert len(all_jobs) == 2
        assert all("不得混入" not in j.input_snapshot["prompt"] for j in all_jobs)
        assert all(j.input_snapshot["canvas_origin"]["run_id"] == run["id"] for j in all_jobs)
        assert all(
            g.input_refs["canvas_origin"]["run_id"] == run["id"]
            for g in db.scalars(select(Generation))
        )
        assert db.get(Shot, uuid.UUID(ids["shot"])).selected_generation_id is None


def test_dispatch_crash_recovers_existing_job(studio, monkeypatch):
    client, sessions, ids = studio
    path, _ = setup(client, ids)
    run = post_ok(client, path + "/runs", {"revision": 1, "request_key": str(uuid.uuid4())})
    dispatch = jobs.dispatch

    def commit_then_crash(db, job):
        dispatch(db, job)
        raise RuntimeError("process ended after durable dispatch")

    monkeypatch.setattr(jobs, "dispatch", commit_then_crash)
    tick(sessions, run["id"], 2)
    monkeypatch.setattr(jobs, "dispatch", dispatch)
    tick(sessions, run["id"], 8)
    assert client.get(path + "/runs").json()[0]["status"] == "succeeded"
    with sessions() as db:
        assert len(list(db.scalars(select(GenerationJob)))) == 2


def test_failure_retry_keeps_successful_upstream_and_cancel(studio, monkeypatch):
    client, sessions, ids = studio
    path, _ = setup(client, ids)
    run = post_ok(client, path + "/runs", {"revision": 1, "request_key": str(uuid.uuid4())})
    tick(sessions, run["id"], 3)
    with sessions() as db:
        r = db.get(CanvasRun, uuid.UUID(run["id"]))
        first = r.steps["a"]["job_id"]
    from app.modules.generation import service

    submit = service.submit

    def fail(*args, **kwargs):
        raise RuntimeError("preparation failed")

    monkeypatch.setattr(service, "submit", fail)
    tick(sessions, run["id"])
    assert client.get(path + "/runs").json()[0]["status"] == "failed"
    monkeypatch.setattr(service, "submit", submit)
    post_ok(client, path + f"/runs/{run['id']}/control", {"action": "retry"})
    tick(sessions, run["id"], 5)
    result = client.get(path + "/runs").json()[0]
    assert result["status"] == "succeeded" and result["steps"]["a"]["job_id"] == first
    second = post_ok(client, path + "/runs", {"revision": 1, "request_key": str(uuid.uuid4())})
    post_ok(client, path + f"/runs/{second['id']}/control", {"action": "cancel"})
    tick(sessions, second["id"], 3)
    assert client.get(path + "/runs").json()[0]["status"] == "canceled"
    with sessions() as db:
        assert len(list(db.scalars(select(GenerationJob)))) == 2


def test_concurrent_schedulers_and_image_ports(studio, monkeypatch):
    client, sessions, ids = studio
    path, doc = setup(client, ids)
    # A provider contract fixture accepts reference images and records actual bytes.
    caps = MockProvider.capabilities
    submit = MockProvider.submit
    received = []

    def with_reference(self):
        c = caps(self)
        c.features = {"reference"}
        c.max_reference_images = 4
        return c

    def capture(self, req):
        received.append(req.references)
        return submit(self, req)

    monkeypatch.setattr(MockProvider, "capabilities", with_reference)
    monkeypatch.setattr(MockProvider, "submit", capture)
    doc["document"]["edges"][1].update(sourceHandle="image", targetHandle="reference")
    doc["document"]["nodes"][1]["data"]["generate"]["count"] = 2
    assert (
        client.put(
            path, json={"name": doc["name"], "revision": 1, "document": doc["document"]}
        ).status_code
        == 200
    )
    run = post_ok(client, path + "/runs", {"revision": 2, "request_key": str(uuid.uuid4())})
    tick(sessions, run["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: tick(sessions, run["id"]), range(2)))
    tick(sessions, run["id"], 8)
    result = client.get(path + "/runs").json()[0]
    assert result["status"] == "succeeded", result
    assert len(received) == 2 and len(received[1]) == 1
    assert received[1][0].blob_hash == result["steps"]["a"]["output"]["blob_hash"]
    assert received[1][0].data_url.startswith(
        "data:image/png;base64,"
    )  # materialized provider input, not merely a dangling ID
    with sessions() as db:
        first = db.get(Generation, uuid.UUID(result["steps"]["a"]["output"]["generation_id"]))
        assert first.input_refs["variant_index"] == 0
        assert len(list(db.scalars(select(GenerationJob)))) == 2


def test_targeted_run_keeps_only_ancestors_and_group_container(studio):
    client, sessions, ids = studio
    path, doc = setup(client, ids)
    graph = copy.deepcopy(doc["document"])
    graph["nodes"].append(node("unconfigured", "generate"))
    graph["nodes"].insert(0, node("group", "group"))
    graph["nodes"][2]["parentId"] = "group"  # a, after group and idea
    assert (
        client.put(path, json={"name": doc["name"], "revision": 1, "document": graph}).status_code
        == 200
    )
    request = {"revision": 2, "request_key": str(uuid.uuid4()), "target_node_id": "a"}
    run = post_ok(client, path + "/runs", request)
    assert set(run["steps"]) == {"group", "idea", "a"}
    assert run["target_node_id"] == "a"
    assert post_ok(client, path + "/runs", request)["id"] == run["id"]
    assert client.post(path + "/runs", json={**request, "target_node_id": "b"}).status_code == 409
    tick(sessions, run["id"], 8)
    result = client.get(path + "/runs").json()[0]
    assert result["status"] == "succeeded", result
    with sessions() as db:
        assert len(list(db.scalars(select(GenerationJob)))) == 1
        frozen = db.get(CanvasRun, uuid.UUID(run["id"])).snapshot
        assert {n["id"] for n in frozen["nodes"]} == {"group", "idea", "a"}
    # Descendants and incomplete sibling branches were never dispatched.
    assert len(client.get(path).json()["document"]["nodes"]) == 5


def test_targeted_run_rejects_unknown_and_non_generation_nodes(studio):
    client, sessions, ids = studio
    path, _ = setup(client, ids)
    for target in ("unknown", "idea"):
        result = client.post(
            path + "/runs",
            json={"revision": 1, "request_key": str(uuid.uuid4()), "target_node_id": target},
        )
        assert result.status_code == 422, result.text
    assert client.get(path + "/runs").json() == []
    request = {"revision": 1, "request_key": str(uuid.uuid4()), "target_node_id": "b"}
    run = post_ok(client, path + "/runs", request)
    assert set(run["steps"]) == {"idea", "a", "b"}
    tick(sessions, run["id"], 10)
    assert client.get(path + "/runs").json()[0]["status"] == "succeeded"
    with sessions() as db:
        assert len(list(db.scalars(select(GenerationJob)))) == 2
