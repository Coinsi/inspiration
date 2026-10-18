"""One adoption boundary for editor projection and native engineering documents."""

# ruff: noqa: F811
import uuid

from app.models.timeline import Timeline
from tests.test_engineering import accept
from tests.test_engineering_review import ready, preview
from tests.test_media_workflow import studio, post_ok  # noqa: F401


def proposal(client, path, tid, url):
    document = client.get(url).json()
    document["items"][0]["duration_ms"] = 600
    native_id = str(uuid.uuid4())
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {"operation": "timeline.edit", "timeline_id": tid, "document": document},
                {
                    "operation": "document.put",
                    "document_id": native_id,
                    "expected_revision": 0,
                    "kind": "image.document",
                    "title": "海报工程",
                    "payload": {"canvas": {"width": 16, "height": 16}, "layers": []},
                },
            ],
        },
    )
    return change, native_id


def test_mixed_adoption_changes_editor_and_native_roots_together(studio):
    client, _, _, path, tid, url = ready(studio)
    original = client.get(url).json()
    change, native_id = proposal(client, path, tid, url)
    assert client.get(url).json() == original
    assert native_id not in client.get(path).json()["snapshot"].get("native_roots", {})
    rendered = preview(client, path, change)
    assert rendered["preview"]["status"] == "succeeded", rendered
    accept(client, path, change)
    assert client.get(url).json()["items"][0]["duration_ms"] == 600
    state = client.get(path).json()
    assert state["revision"] == 1
    assert native_id in state["snapshot"]["native_roots"]


def test_editor_conflict_does_not_partially_adopt_native_document(studio):
    client, sessions, _, path, tid, url = ready(studio)
    change, native_id = proposal(client, path, tid, url)
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    with sessions() as db:
        db.get(Timeline, uuid.UUID(tid)).revision += 1
        db.commit()
    result = client.post(
        path + f"/changes/{change['id']}/accept", json={"fingerprint": change["fingerprint"]}
    )
    assert result.status_code == 409
    state = client.get(path).json()
    assert state["revision"] == 0
    assert native_id not in state["snapshot"].get("native_roots", {})
    assert client.get(url).json()["items"][0]["duration_ms"] == 1000


def test_mixed_outputs_keep_separate_previews_and_adoption_evidence(studio):
    client, _, _, path, tid, url = ready(studio)
    change, native_id = proposal(client, path, tid, url)
    endpoint = path + f"/changes/{change['id']}/preview"
    image = post_ok(
        client, endpoint, {"fingerprint": change["fingerprint"], "output_id": native_id}
    )
    # A picture preview cannot satisfy the timeline adoption gate.
    refused = client.post(
        path + f"/changes/{change['id']}/accept", json={"fingerprint": change["fingerprint"]}
    )
    assert refused.status_code == 409
    video = preview(client, path, change)
    assert video["preview"]["output_type"] == "video"
    again = post_ok(
        client, endpoint, {"fingerprint": change["fingerprint"], "output_id": native_id}
    )
    assert again["preview_job_id"] == image["preview_job_id"]
    detail = client.get(path + f"/changes/{change['id']}").json()
    assert len(detail["previews"]) == 2
    assert {p["output_type"] for p in detail["previews"]} == {"image", "video"}
    assert detail["preview"]["job_id"] == video["preview"]["job_id"]
    accept(client, path, change)
