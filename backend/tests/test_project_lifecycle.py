"""Reversible project deletion preserves media and enforces access boundaries."""

# ruff: noqa: F811
import io
import uuid

from PIL import Image
from sqlalchemy import select

from app.models.identity import AuditLog, Membership, Project
from app.models.shot import Shot
from tests.test_media_workflow import studio  # noqa: F401


def test_project_trash_restore_and_shared_media(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    buffer = io.BytesIO()
    Image.new("RGB", (32, 18), "purple").save(buffer, "PNG")
    raw = buffer.getvalue()
    g = client.post(
        root + f"/media/shot/{ids['shot']}/upload", files={"file": ("a.png", raw, "image/png")}
    ).json()
    assert g.get("output_blob_hash"), g
    with sessions() as db:
        target = db.get(Project, ids["other"])
        target.cover_blob_hash = g["output_blob_hash"]
        db.add(Membership(project_id=target.id, user_id=ids["user"], role="admin"))
        db.commit()
    assert client.delete(root).status_code == 204
    assert client.delete(root).status_code == 204
    assert not any(p["id"] == ids["project"] for p in client.get("/api/v1/projects").json())
    trash = client.get("/api/v1/projects?deleted=true").json()
    assert len(trash) == 1 and trash[0]["id"] == ids["project"] and trash[0]["deleted_at"]
    for path in (
        root,
        root + "/shots",
        root + "/canvases",
        root + "/blobs/" + g["output_blob_hash"],
    ):
        assert client.get(path).status_code == 403
    other = f"/api/v1/projects/{ids['other']}"
    assert client.get(other + "/blobs/" + g["output_blob_hash"]).content == raw
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])) is not None
        assert (
            len(list(db.scalars(select(AuditLog).where(AuditLog.action == "project.delete")))) == 1
        )
    restored = client.post(root + "/restore")
    assert restored.status_code == 200 and restored.json()["deleted_at"] is None
    assert restored.json()["code"] == "TEST"
    assert client.get(root + "/blobs/" + g["output_blob_hash"]).content == raw
    assert client.get("/api/v1/projects?deleted=true").json() == []
    assert client.post(root + "/restore").status_code == 200


def test_trash_admin_permissions(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    assert client.delete(f"/api/v1/projects/{ids['other']}").status_code == 403
    assert client.post(f"/api/v1/projects/{ids['other']}/restore").status_code == 403
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert client.delete(root).status_code == 403
    assert client.post(root + "/restore").status_code == 403
    assert client.get(root).status_code == 200
    with sessions() as db:
        from datetime import UTC, datetime

        db.get(Project, uuid.UUID(ids["project"])).deleted_at = datetime.now(UTC)
        db.commit()
    assert client.get("/api/v1/projects?deleted=true").json() == []
    assert client.post(root + "/restore").status_code == 403
