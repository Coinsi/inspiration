"""Library sources work without shots and retain a fixed original version."""

# ruff: noqa: F811
import uuid

from app.models.library import MediaVersion
from tests.test_library_materialization import seed
from tests.test_media_workflow import changing_video, post_ok, studio  # noqa: F401


def test_library_clip_roundtrip_render_and_scope(studio, changing_video):
    client, sessions, ids = studio
    _, version, _ = seed(sessions, ids, changing_video)
    root = f"/api/v1/projects/{ids['project']}"
    timeline = post_ok(client, root + "/timelines", {"name": "Direct library edit"})
    base = root + f"/timelines/{timeline['id']}"
    endpoint = base + f"/library-sources/{version}"
    source = post_ok(client, endpoint, {})
    assert post_ok(client, endpoint, {})["id"] == source["id"]
    doc = client.get(base + "/document").json()
    doc["items"] = [
        {
            "id": str(uuid.uuid4()),
            "shot_id": None,
            "generation_id": source["id"],
            "in_point_ms": 0,
            "out_point_ms": 0,
            "duration_ms": 600,
        }
    ]
    saved = client.put(base + "/document", json=doc)
    assert saved.status_code == 200, saved.text
    assert client.get(base + "/document").json()["items"][0]["shot_id"] is None
    job = post_ok(
        client,
        base + "/render",
        {"height": 720, "aspect_ratio": "16:9", "revision": saved.json()["revision"]},
    )
    assert job["status"] == "succeeded", job
    foreign = (
        f"/api/v1/projects/{ids['other']}/timelines/{timeline['id']}/library-sources/{version}"
    )
    assert client.post(foreign, json={}).status_code in (403, 404)
    with sessions() as db:
        db.get(MediaVersion, uuid.UUID(version)).status = "processing"
        db.commit()
    assert client.post(endpoint, json={}).status_code == 409


def test_audio_library_source_can_be_mixed(studio):
    import io
    import wave

    from app.models.library import LibraryMedia
    from app.storage import cas
    from tests.test_engineering_review import ready

    client, sessions, ids, _, tid, url = ready(studio)
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    with sessions() as db:
        blob = cas.put_bytes(db, data.getvalue(), "audio/wav", duration_ms=1000)
        media = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="背景声音")
        db.add(media)
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            ordinal=1,
            media_type="audio",
            filename="sound.wav",
            fingerprint="b" * 64,
            size_bytes=len(data.getvalue()),
            uploaded_bytes=len(data.getvalue()),
            status="ready",
            duration_ms=1000,
            original_hash=blob.hash,
        )
        db.add(version)
        db.commit()
        version_id = str(version.id)
    base = f"/api/v1/projects/{ids['project']}/timelines/{tid}"
    source = post_ok(client, base + f"/library-sources/{version_id}", {})
    doc = client.get(url).json()
    doc["audio"] = [
        {
            "id": str(uuid.uuid4()),
            "generation_id": source["id"],
            "name": "背景声音",
            "kind": "music",
            "start_ms": 0,
            "in_point_ms": 0,
            "duration_ms": 600,
            "gain_db": 0,
            "fade_in_ms": 0,
            "fade_out_ms": 0,
            "muted": False,
        }
    ]
    saved = client.put(url, json=doc)
    assert saved.status_code == 200, saved.text
    job = post_ok(
        client,
        base + "/render",
        {"height": 720, "aspect_ratio": "16:9", "revision": saved.json()["revision"]},
    )
    assert job["status"] == "succeeded", job
