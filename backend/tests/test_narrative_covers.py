"""Cover lifecycle keeps story content intact and media scoped to the owning project."""

# ruff: noqa: F811
import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import select

from app.models.identity import Membership
from tests.test_media_workflow import studio  # noqa: F401


def image_bytes(color="red", fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (120, 80), color).save(stream, fmt)
    return stream.getvalue()


def create_target(client, root, kind):
    if kind == "novels":
        result = client.post(
            root + "/novels/import",
            files={
                "file": ("story.txt", "第一章 雨夜\n窗外的雨停了。".encode(), "text/plain"),
            },
        )
    else:
        result = client.post(root + "/scripts", json={"title": "雨夜"})
    assert result.status_code == 200, result.text
    return result.json()["id"]


@pytest.mark.parametrize("kind", ["novels", "scripts"])
def test_cover_lifecycle_preserves_body_and_project_boundary(studio, kind):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    ident = create_target(client, root, kind)
    path = f"{root}/{kind}/{ident}"
    original = client.get(path).json()
    raw = image_bytes()
    uploaded = client.post(path + "/cover", files={"file": ("cover.png", raw, "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    digest = uploaded.json()["cover_blob_hash"]
    assert digest
    assert client.get(root + "/blobs/" + digest).content == raw
    detail = client.get(path).json()
    assert {k: v for k, v in detail.items() if k != "cover_blob_hash"} == {
        k: v for k, v in original.items() if k != "cover_blob_hash"
    }
    assert (
        next(x for x in client.get(root + "/" + kind).json() if x["id"] == ident)["cover_blob_hash"]
        == digest
    )
    renamed = client.patch(path, json={"title": "雨停之后"})
    assert renamed.status_code == 200
    assert renamed.json()["cover_blob_hash"] == digest
    # Even a member of both projects cannot read or change a different project's cover.
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    other = f"/api/v1/projects/{ids['other']}"
    assert client.get(other + "/blobs/" + digest).status_code == 404
    assert (
        client.post(
            f"{other}/{kind}/{ident}/cover", files={"file": ("x.png", raw, "image/png")}
        ).status_code
        == 404
    )
    second = client.post(
        path + "/cover", files={"file": ("blue.png", image_bytes("blue"), "image/png")}
    )
    assert second.status_code == 200
    assert second.json()["cover_blob_hash"] != digest
    assert client.get(root + "/blobs/" + digest).status_code == 404
    assert client.delete(path + "/cover").status_code == 200
    assert client.get(path).json()["cover_blob_hash"] is None
    assert client.delete(path + "/cover").status_code == 200


@pytest.mark.parametrize("kind", ["novels", "scripts"])
def test_cover_validation_permissions_and_deleted_target(studio, kind):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    ident = create_target(client, root, kind)
    path = f"{root}/{kind}/{ident}"
    raw = image_bytes()
    initial = client.post(path + "/cover", files={"file": ("cover.png", raw, "image/png")}).json()
    for invalid in (b"not an image", image_bytes(fmt="GIF"), b"x" * (10 * 1024 * 1024 + 1)):
        assert (
            client.post(
                path + "/cover", files={"file": ("fake.png", invalid, "image/png")}
            ).status_code
            == 422
        )
        assert client.get(path).json()["cover_blob_hash"] == initial["cover_blob_hash"]
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert (
        client.post(path + "/cover", files={"file": ("c.png", raw, "image/png")}).status_code == 403
    )
    assert client.delete(path + "/cover").status_code == 403
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "admin"
        db.commit()
    assert client.delete(path).status_code == 204
    assert client.delete(path + "/cover").status_code == 404
    assert (
        client.post(path + "/cover", files={"file": ("c.png", raw, "image/png")}).status_code == 404
    )
    if kind == "novels":
        assert client.post(path + "/restore").status_code == 200
        assert client.get(path).json()["cover_blob_hash"] == initial["cover_blob_hash"]
