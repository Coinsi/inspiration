"""End-to-end candidate rendering/adoption with real isolated DB and FFmpeg."""

# ruff: noqa: F811
import uuid

from sqlalchemy import select

from app.models.generation import Generation
from app.models.timeline import TimelineItem
from app.modules.generation import media_engine
from app.storage import cas
from tests.test_engineering import accept
from tests.test_engineering_timeline import prepared
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def ready(studio):
    client, sessions, ids, path, tid, _ = prepared(studio)
    with sessions() as db:
        item = db.scalar(select(TimelineItem).where(TimelineItem.timeline_id == uuid.UUID(tid)))
        blob = cas.put_bytes(db, media_engine.mock_image(), "image/png")
        db.get(Generation, item.generation_id).output_blob_hash = blob.hash
        db.commit()
    url = f"/api/v1/projects/{ids['project']}/timelines/{tid}/document"
    return client, sessions, ids, path, tid, url


def draft(client, path, tid, url, revision=0, duration=600):
    doc = client.get(url).json()
    doc["items"][0]["duration_ms"] = duration
    return post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": revision,
            "commands": [{"operation": "timeline.edit", "timeline_id": tid, "document": doc}],
        },
    )


def preview(client, path, change, retry=None):
    response = post_ok(
        client,
        path + f"/changes/{change['id']}/preview",
        {
            "fingerprint": change["fingerprint"],
            "retry_job_id": retry,
        },
    )
    return client.get(path + f"/changes/{response['id']}").json()


def test_real_preview_adopt_and_historical_restore(studio):
    client, sessions, ids, path, tid, url = ready(studio)
    original = client.get(url).json()
    first = draft(client, path, tid, url)
    assert client.get(url).json() == original
    assert (
        client.post(
            path + f"/changes/{first['id']}/accept",
            json={
                "fingerprint": first["fingerprint"],
            },
        ).status_code
        == 409
    )
    rendered = preview(client, path, first)
    assert rendered["preview"]["status"] == "succeeded", rendered
    assert rendered["preview"]["blob_hash"]
    assert preview(client, path, first)["preview_job_id"] == rendered["preview_job_id"]
    accept(client, path, first)
    saved = client.get(url).json()
    assert saved["revision"] == 1 and saved["items"][0]["duration_ms"] == 600
    assert saved["items"][0]["id"] == original["items"][0]["id"]
    assert accept(client, path, first)["committed_revision"] == 1
    second = draft(client, path, tid, url, revision=1, duration=900)
    assert preview(client, path, second)["preview"]["status"] == "succeeded"
    accept(client, path, second)
    restore = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 2,
            "commands": [
                {
                    "operation": "timeline.restore",
                    "timeline_id": tid,
                    "timeline_revision": 2,
                    "engineering_revision": 1,
                }
            ],
        },
    )
    assert client.get(url).json()["items"][0]["duration_ms"] == 900
    assert preview(client, path, restore)["preview"]["status"] == "succeeded"
    accept(client, path, restore)
    restored = client.get(url).json()
    assert restored["revision"] == 3 and restored["items"][0]["duration_ms"] == 600
    assert restored["items"][0]["id"] == original["items"][0]["id"]
    assert len(client.get(path + "/history").json()) == 3


def test_external_edit_after_preview_blocks_adoption(studio):
    client, _, _, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    external = client.get(url).json()
    external["items"][0]["note"] = "另一个编辑者的修改"
    response = client.put(url, json=external)
    assert response.status_code == 200
    assert (
        client.post(
            path + f"/changes/{change['id']}/accept",
            json={
                "fingerprint": change["fingerprint"],
            },
        ).status_code
        == 409
    )
    assert client.get(url).json()["items"][0]["note"] == external["items"][0]["note"]
    assert client.get(path).json()["revision"] == 0


def test_failed_preview_retries_without_duplicate_requests(studio, monkeypatch):
    client, _, _, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    real = media_engine.render_file

    def fail(*args, **kwargs):
        raise ValueError("test render unavailable")

    monkeypatch.setattr(media_engine, "render_file", fail)
    failed = preview(client, path, change)
    assert failed["preview"]["status"] == "failed"
    assert preview(client, path, change)["preview_job_id"] == failed["preview_job_id"]
    assert (
        client.post(
            path + f"/changes/{change['id']}/accept",
            json={
                "fingerprint": change["fingerprint"],
            },
        ).status_code
        == 409
    )
    monkeypatch.setattr(media_engine, "render_file", real)
    retried = preview(client, path, change, failed["preview_job_id"])
    assert retried["preview"]["status"] == "succeeded"
    assert retried["preview_job_id"] != failed["preview_job_id"]
    assert (
        preview(client, path, change, failed["preview_job_id"])["preview_job_id"]
        == retried["preview_job_id"]
    )


def test_stable_ids_survive_save_and_split_and_duplicate_ids_rejected(studio):
    client, _, _, _, _, url = ready(studio)
    doc = client.get(url).json()
    first_id = doc["items"][0]["id"]
    doc["items"].append({**doc["items"][0], "id": str(uuid.uuid4())})
    response = client.put(url, json=doc)
    assert response.status_code == 200
    saved = response.json()
    saved["items"].reverse()
    moved = client.put(url, json=saved).json()
    assert moved["items"][1]["id"] == first_id
    moved["items"][0]["id"] = first_id
    assert client.put(url, json=moved).status_code == 422


def test_preview_request_cannot_render_another_work_candidate(studio):
    client, _, ids, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    other = post_ok(client, f"/api/v1/projects/{ids['project']}/works", {"title": "另一个作品"})
    wrong = f"/api/v1/projects/{ids['project']}/works/{other['id']}/engineering"
    assert (
        client.post(
            wrong + f"/changes/{change['id']}/preview",
            json={
                "fingerprint": change["fingerprint"],
            },
        ).status_code
        == 404
    )
