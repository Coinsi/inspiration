"""Real project creation, cover lifecycle, privacy and retry behavior."""

# ruff: noqa: F811
import io
import uuid
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
from sqlalchemy import func, select

from app.models.identity import Membership, Project
from tests.test_media_workflow import studio  # noqa: F401


def picture(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (160, 90), color).save(out, "PNG")
    return out.getvalue()


def test_name_only_creation_and_atomic_cover_retry(studio):
    client, sessions, ids = studio
    first = client.post("/api/v1/projects", json={"name": "  新故事  "})
    second = client.post("/api/v1/projects", json={"name": "新故事"})
    assert first.status_code == second.status_code == 200
    assert first.json()["name"] == "新故事"
    assert first.json()["code"] != second.json()["code"]
    assert first.json()["cover_blob_hash"] is None
    assert client.post("/api/v1/projects", json={"name": "  "}).status_code == 422
    assert client.post("/api/v1/projects", json={"name": "兼容", "code": "TEST"}).status_code == 409
    key = str(uuid.uuid4())

    def create():
        return client.post("/api/v1/projects/with-cover", data={"name": "带封面故事", "request_key": key},
                           files={"file": ("cover.png", picture(), "image/png")})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(), range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    p = responses[0].json()
    assert p["id"] == responses[1].json()["id"] == key
    assert p["cover_blob_hash"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Project).where(Project.name == "带封面故事")) == 1
    before = len(client.get("/api/v1/projects").json())
    broken = client.post("/api/v1/projects/with-cover", data={"name": "失败无空项目", "request_key": str(uuid.uuid4())},
                         files={"file": ("bad.png", b"invalid", "image/png")})
    assert broken.status_code == 422
    assert len(client.get("/api/v1/projects").json()) == before


def test_cover_replace_remove_and_project_permissions(studio):
    client, sessions, ids = studio
    root = f'/api/v1/projects/{ids["project"]}'
    raw = picture()
    response = client.post(root + "/cover", files={"file": ("a.png", raw, "image/png")})
    assert response.status_code == 200, response.text
    digest = response.json()["cover_blob_hash"]
    assert client.get(root + "/blobs/" + digest).content == raw
    assert client.get(f'/api/v1/projects/{ids["other"]}/blobs/{digest}').status_code == 403
    assert client.post(root + "/cover", files={"file": ("bad.svg", b"<svg/>", "image/svg+xml")}).status_code == 422
    assert client.get(root).json()["cover_blob_hash"] == digest
    updated = client.post(root + "/cover", files={"file": ("b.png", picture("blue"), "image/png")})
    assert updated.status_code == 200
    assert updated.json()["cover_blob_hash"] != digest
    assert client.get(root + "/blobs/" + digest).status_code == 404
    with sessions() as db:
        member = db.scalar(select(Membership).where(Membership.project_id == uuid.UUID(ids["project"])))
        member.role = "viewer"
        db.commit()
    assert client.post(root + "/cover", files={"file": ("a.png", raw, "image/png")}).status_code == 403
    assert client.delete(root + "/cover").status_code == 403
    assert client.get(root + "/blobs/" + updated.json()["cover_blob_hash"]).status_code == 200
    with sessions() as db:
        member = db.scalar(select(Membership).where(Membership.project_id == uuid.UUID(ids["project"])))
        member.role = "admin"
        db.commit()
    assert client.delete(root + "/cover").json()["cover_blob_hash"] is None
    assert client.get(root).json()["code"] == "TEST"
