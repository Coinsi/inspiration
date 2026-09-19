"""Actual canvas import, export, mentions and cache correctness in isolated schemas."""

# ruff: noqa: F811
import copy
import io
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
from sqlalchemy import func, select

from app.adapters.generation.mock import MockProvider
from app.models.asset import Asset
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership
from tests.test_canvas import setup, tick
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def png(color="red"):
    out = io.BytesIO()
    Image.new("RGBA", (53, 37), color).save(out, "PNG")
    return out.getvalue()


def test_direct_import_retry_derivation_and_bundle(studio):
    client, sessions, ids = studio
    path, doc = setup(client, ids)
    key = str(uuid.uuid4())

    def upload(data=None, content=None):
        return client.post(
            path + "/import",
            data=data or {"request_key": key},
            files={"file": ("source.png", content or png(), "image/png")},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(), range(2)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    g = responses[0].json()
    assert g["id"] == responses[1].json()["id"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 1
        assert db.scalar(select(func.count()).select_from(Generation)) == 1
    assert upload(content=png("blue")).status_code == 409
    assert upload({"request_key": str(uuid.uuid4())}, b"broken image").status_code == 422
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 1
    edited = upload(
        {"request_key": str(uuid.uuid4()), "source_generation_id": g["id"]}, png("blue")
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["target_id"] == g["target_id"]
    assert edited.json()["input_refs"]["source_generation_id"] == g["id"]
    node = doc["document"]["nodes"][0]
    node["data"].update(
        kind="asset",
        target_type="asset",
        target_id=g["target_id"],
        generation_id=g["id"],
        label='<script>alert("test")</script>',
        text="原片说明",
    )
    saved = client.put(
        path,
        json={
            "name": "离线作品包",
            "revision": 1,
            "document": {"nodes": [node], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        },
    )
    assert saved.status_code == 200, saved.text
    exported = client.get(path + "/export")
    assert exported.status_code == 200, exported.text[:100] if exported.status_code != 200 else ""
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        page = archive.read("index.html").decode()
        assert '<script>alert("test")' not in page and "&lt;script&gt;" in page
        assert "Authorization" not in page and "token=" not in page
        media = [name for name in archive.namelist() if name.startswith("media/")]
        assert len(media) == 1
        assert Image.open(io.BytesIO(archive.read(media[0]))).size == (53, 37)
    assert (
        client.get(path.replace(ids["project"], str(ids["other"])) + "/export").status_code == 403
    )
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert upload().status_code == 403
    assert client.get(path + "/export").status_code == 200


def test_reuse_unchanged_is_opt_in_and_invalidates_transitively(studio):
    client, sessions, ids = studio
    path, doc = setup(client, ids)

    def run(revision=1, **extra):
        created = post_ok(
            client,
            path + "/runs",
            {"revision": revision, "request_key": str(uuid.uuid4()), **extra},
        )
        tick(sessions, created["id"], 12)
        result = next(r for r in client.get(path + "/runs").json() if r["id"] == created["id"])
        assert result["status"] == "succeeded", result
        return result

    def count():
        with sessions() as db:
            return db.scalar(select(func.count()).select_from(GenerationJob))

    first = run()
    assert count() == 2
    cached = run(reuse_unchanged=True)
    assert count() == 2 and cached["steps"]["a"]["reused"] and cached["steps"]["b"]["reused"]
    assert cached["steps"]["a"]["output"] == first["steps"]["a"]["output"]
    target = run(reuse_unchanged=True, target_node_id="b")
    assert (
        count() == 3 and target["steps"]["a"]["reused"] and not target["steps"]["b"].get("reused")
    )
    # Same prompt, new stochastic upstream output: never reuse its old dependent result.
    upstream = run(reuse_unchanged=True, target_node_id="a")
    assert count() == 4
    renewed = run(reuse_unchanged=True)
    assert count() == 5 and renewed["steps"]["a"]["reused"]
    assert renewed["steps"]["a"]["output"] == upstream["steps"]["a"]["output"]
    assert not renewed["steps"]["b"].get("reused")
    changed = copy.deepcopy(doc["document"])
    changed["nodes"][0]["data"]["text"] = "上游已改变"
    assert (
        client.put(path, json={"name": "测试画布", "revision": 1, "document": changed}).status_code
        == 200
    )
    refreshed = run(2, reuse_unchanged=True)
    assert (
        count() == 7
        and not refreshed["steps"]["a"].get("reused")
        and not refreshed["steps"]["b"].get("reused")
    )
    run(2)
    assert count() == 9


def test_smart_mentions_resolve_to_actual_ordered_references(studio, monkeypatch):
    client, sessions, ids = studio
    path, doc = setup(client, ids)
    capabilities = MockProvider.capabilities
    submit = MockProvider.submit
    captured = []

    def caps(self):
        c = capabilities(self)
        c.features = {"reference"}
        c.max_reference_images = 4
        return c

    def capture(self, request):
        captured.append(request)
        return submit(self, request)

    monkeypatch.setattr(MockProvider, "capabilities", caps)
    monkeypatch.setattr(MockProvider, "submit", capture)
    document = doc["document"]
    document["edges"][1].update(sourceHandle="image", targetHandle="reference")
    document["nodes"][2]["data"].update(
        text="保持 @【主角】 的服装", mentions=[{"node_id": "a", "alias": "主角"}]
    )
    response = client.put(path, json={"name": "智能引用", "revision": 1, "document": document})
    assert response.status_code == 200, response.text
    run = post_ok(client, path + "/runs", {"revision": 2, "request_key": str(uuid.uuid4())})
    tick(sessions, run["id"], 12)
    assert len(captured) == 2
    assert "[参考图1：主角]" in captured[1].prompt and "@【" not in captured[1].prompt
    assert len(captured[1].references) == 1 and captured[1].references[0].blob_hash
    invalid = copy.deepcopy(document)
    invalid["nodes"][2]["data"]["mentions"][0]["node_id"] = "missing"
    assert (
        client.put(path, json={"name": "错误引用", "revision": 2, "document": invalid}).status_code
        == 422
    )
