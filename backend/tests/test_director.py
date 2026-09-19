"""Scene revision concurrency, bounds, project permissions and export provenance."""

# ruff: noqa: F811
import io
import uuid

from PIL import Image
from sqlalchemy import select

from app.models.generation import Generation
from app.models.identity import Membership
from app.models.shot import Shot
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_scene_history_and_validation(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/director-scenes"
    c = post_ok(client, root, {"name": "双人预演"})
    doc = c["document"]
    doc["objects"] = [{"id": str(uuid.uuid4()), "name": "人物", "position": [1, 0, 0]}]
    r = client.put(root + "/" + c["id"], json={"revision": 0, "name": c["name"], "document": doc})
    assert r.status_code == 200
    assert (
        client.put(
            root + "/" + c["id"], json={"revision": 0, "name": c["name"], "document": doc}
        ).status_code
        == 409
    )
    doc["objects"][0]["position"] = [9999, 0, 0]
    assert (
        client.put(
            root + "/" + c["id"], json={"revision": 1, "name": c["name"], "document": doc}
        ).status_code
        == 422
    )
    restored = post_ok(client, root + "/" + c["id"] + "/history/0/restore", {"revision": 1})
    assert restored["revision"] == 2 and restored["document"]["objects"] == []
    assert len(client.get(root + "/" + c["id"] + "/history").json()) == 3
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert client.get(root + "/" + c["id"]).status_code == 200
    assert client.post(root, json={"name": "禁止"}).status_code == 403


def test_reference_export_is_pinned_idempotent_and_not_selected(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/director-scenes"
    c = post_ok(client, root, {"name": "参考验收"})
    buf = io.BytesIO()
    Image.new("RGB", (160, 90), "navy").save(buf, "PNG")
    raw = buf.getvalue()
    data = {
        "revision": 0,
        "shot_id": ids["shot"],
        "request_key": str(uuid.uuid4()),
        "progress": 0.5,
    }

    def export(data=data, raw=raw):
        return client.post(
            root + "/" + c["id"] + "/references",
            data=data,
            files={"file": ("frame.png", raw, "image/png")},
        )

    r = export()
    assert r.status_code == 200, r.text
    gen = r.json()
    assert (
        gen["input_refs"]["revision"] == 0
        and gen["input_refs"]["progress"] == 0.5
        and not gen["is_selected"]
    )
    assert export().json()["id"] == gen["id"]
    assert export({**data, "progress": 0.6}).status_code == 409
    assert export({**data, "request_key": str(uuid.uuid4()), "revision": 1}).status_code == 409
    assert export({**data, "request_key": str(uuid.uuid4())}, b"invalid").status_code == 422
    assert (
        export({**data, "request_key": str(uuid.uuid4()), "shot_id": str(uuid.uuid4())}).status_code
        == 404
    )
    with sessions() as db:
        shot = db.get(Shot, uuid.UUID(ids["shot"]))
        assert shot.selected_generation_id is None
        shot.status = "locked"
        db.commit()
    assert export({**data, "request_key": str(uuid.uuid4())}).status_code == 423
    with sessions() as db:
        assert len(list(db.scalars(select(Generation)))) == 1
