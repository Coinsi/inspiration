"""Project edits are atomic and restricted to project administrators."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import select

from app.models.identity import Membership, Project
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_narrative_covers import image_bytes


def test_edit_profile_preserves_identity_content_and_cover(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    original = client.get(root).json()
    raw = image_bytes()
    result = client.post(
        root + "/profile",
        data={"name": "  新项目名  ", "description": "  新简介  "},
        files={"file": ("cover.png", raw, "image/png")},
    )
    assert result.status_code == 200, result.text
    updated = result.json()
    assert updated["name"] == "新项目名" and updated["description"] == "新简介"
    for key in ("id", "code", "owner_id", "settings", "created_at"):
        assert updated[key] == original[key]
    digest = updated["cover_blob_hash"]
    assert client.get(root + "/blobs/" + digest).content == raw
    assert client.get(root + "/scripts").json()[0]["title"] == "Test script"
    assert (
        next(p for p in client.get("/api/v1/projects").json() if p["id"] == ids["project"])["name"]
        == "新项目名"
    )
    renamed = client.post(root + "/profile", data={"name": "再改名", "description": ""})
    assert renamed.status_code == 200
    assert renamed.json()["description"] is None
    assert renamed.json()["cover_blob_hash"] == digest
    removed = client.post(root + "/profile", data={"name": "再改名", "remove_cover": "true"})
    assert removed.status_code == 200 and removed.json()["cover_blob_hash"] is None
    assert client.get(root).json() == removed.json()


def test_invalid_profile_does_not_partially_save(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    original = client.get(root).json()
    for payload in (
        {"name": "   "},
        {"name": "x" * 256},
        {"name": "Good", "description": "x" * 10001},
    ):
        assert client.post(root + "/profile", data=payload).status_code == 422
        assert client.get(root).json() == original
    for remove in ("false", "true"):
        response = client.post(
            root + "/profile",
            data={"name": "不得保存", "remove_cover": remove},
            files={"file": ("broken.png", b"invalid", "image/png")},
        )
        assert response.status_code == 422
        assert client.get(root).json() == original


@pytest.mark.parametrize("role", ["viewer", "writer", "director"])
def test_profile_rejects_non_admin_and_other_deleted_projects(studio, role):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    assert (
        client.post(f"/api/v1/projects/{ids['other']}/profile", data={"name": "No"}).status_code
        == 403
    )
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = role
        db.commit()
    assert client.post(root + "/profile", data={"name": "No"}).status_code == 403
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "admin"
        db.commit()
    assert client.delete(root).status_code == 204
    assert client.post(root + "/profile", data={"name": "No"}).status_code == 403
    with sessions() as db:
        assert db.get(Project, uuid.UUID(ids["project"])).name == "Media workflow test"
