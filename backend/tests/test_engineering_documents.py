"""Real DB and render acceptance for independently versioned engineering documents."""

# ruff: noqa: F811
import uuid

from app.models.engineering import EngineeringDocumentRevision
from app.models.timeline import Timeline
from tests.test_engineering import accept
from tests.test_engineering_review import draft, preview, ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def view(client, path, ref):
    response = client.get(f"{path}/documents/{ref['id']}/revisions/{ref['revision']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_working_save_reopen_fixed_candidate_render_restore(studio):
    client, sessions, ids, path, tid, url = ready(studio)
    root = path.removesuffix("/engineering")
    post_ok(client, root + "/timelines", {"timeline_id": tid})
    docs = client.get(path + "/documents").json()["documents"]
    seq = next(d for d in docs if d["kind"] == "sequence.timeline")
    first_ref = {"id": seq["id"], "revision": seq["working_revision"]}
    original = view(client, path, first_ref)
    assert original["inputs"] and original["payload"]["editor"]["items"]
    # A real normal editor save advances the working doc, not the adopted HEAD.
    content = client.get(url).json()
    content["items"][0]["duration_ms"] = 700
    saved = client.put(url, json=content)
    assert saved.status_code == 200, saved.text
    seq2 = next(
        d for d in client.get(path + "/documents").json()["documents"] if d["id"] == seq["id"]
    )
    assert seq2["working_revision"] != first_ref["revision"]
    assert client.get(path).json()["revision"] == 0
    assert view(client, path, first_ref) == original
    # Open an old document revision as a new candidate, then truly render/adopt.
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.edit",
                    "document_id": seq["id"],
                    "document_revision": first_ref["revision"],
                    "timeline_revision": saved.json()["revision"],
                }
            ],
        },
    )
    assert change["timeline_update"]["document_ref"] == first_ref
    assert client.get(url).json()["items"][0]["duration_ms"] == 700
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    accept(client, path, change)
    assert (
        client.get(url).json()["items"][0]["duration_ms"]
        == original["payload"]["editor"]["items"][0]["duration_ms"]
    )
    assert client.get(path).json()["snapshot"]["document_roots"][tid] == first_ref
    assert view(client, path, first_ref) == original


def test_empty_work_document_and_idempotent_start(studio):
    client, _, ids, path, _, _ = ready(studio)
    root = path.removesuffix("/engineering")
    timeline = post_ok(client, root + "/timelines", {})
    assert post_ok(client, root + "/timelines", {})["id"] == timeline["id"]
    seq = next(
        d
        for d in client.get(path + "/documents").json()["documents"]
        if d["kind"] == "sequence.timeline"
    )
    assert len(seq["revisions"]) == 1
    data = view(client, path, {"id": seq["id"], "revision": seq["working_revision"]})
    assert data["payload"]["editor"]["items"] == []


def test_document_read_is_work_scoped_and_survives_timeline_trash(studio):
    client, sessions, ids, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    ref = change["timeline_update"]["document_ref"]
    original = view(client, path, ref)
    other = post_ok(client, f"/api/v1/projects/{ids['project']}/works", {"title": "另一个作品"})
    denied = client.get(
        f"/api/v1/projects/{ids['project']}/works/{other['id']}/engineering/documents/{ref['id']}/revisions/{ref['revision']}"
    )
    assert denied.status_code == 404
    from datetime import UTC, datetime

    with sessions() as db:
        db.get(Timeline, uuid.UUID(tid)).deleted_at = datetime.now(UTC)
        db.commit()
    assert view(client, path, ref) == original


def test_corrupted_revision_is_rejected_before_preview(studio):
    client, sessions, _, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    ref = change["timeline_update"]["document_ref"]
    with sessions() as db:
        row = db.get(EngineeringDocumentRevision, (uuid.UUID(ref["id"]), ref["revision"]))
        row.payload = {**row.payload, "aspect_ratio": "1:1"}
        db.commit()
    result = client.post(
        path + f"/changes/{change['id']}/preview", json={"fingerprint": change["fingerprint"]}
    )
    assert result.status_code == 409


def test_document_edit_rejects_stale_projection_and_unknown_operation(studio):
    client, _, _, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    ref = change["timeline_update"]["document_ref"]
    data = client.get(url).json()
    assert client.put(url, json=data).status_code == 200
    result = client.post(
        path + "/changes",
        json={
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.edit",
                    "document_id": ref["id"],
                    "document_revision": ref["revision"],
                    "timeline_revision": 0,
                }
            ],
        },
    )
    assert result.status_code == 409
    invalid = client.post(
        path + "/changes",
        json={
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [{"operation": "document.effect", "kind": "unregistered", "payload": {}}],
        },
    )
    assert invalid.status_code == 422


def test_initial_manifest_and_fixed_dependency_closure(studio):
    client, _, _, path, tid, url = ready(studio)
    empty = client.get(path + "/manifest").json()
    assert empty["revision"] == 0 and empty["manifest_ref"]
    assert len(empty["documents"]) == 1
    assert empty["documents"][0]["kind"] == "work.manifest"
    change = draft(client, path, tid, url)
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    accept(client, path, change)
    bundle = client.get(path + "/manifest").json()
    assert {d["kind"] for d in bundle["documents"]} == {
        "work.manifest",
        "sequence.timeline",
        "resource",
    }
    assert bundle["includes_media_files"] is False
    # Editing from a document revision preserves the old closure after a new adoption.
    ref = change["timeline_update"]["document_ref"]
    editor = client.get(url).json()
    editor["items"][0]["duration_ms"] = 850
    next_change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {
                    "operation": "document.edit",
                    "document_id": ref["id"],
                    "document_revision": ref["revision"],
                    "timeline_revision": editor["revision"],
                    "document": editor,
                    "aspect_ratio": "9:16",
                }
            ],
        },
    )
    assert next_change["timeline_update"]["document_ref"] != ref
    assert preview(client, path, next_change)["preview"]["status"] == "succeeded"
    accept(client, path, next_change)
    assert client.get(path + "/manifest?revision=1").json() == bundle
    assert client.get(path + "/manifest").json()["revision"] == 2
