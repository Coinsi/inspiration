"""Opt-in real standalone image/audio WISE pipelines and audio-reference search.

No model download or mock HTTP/embeddings. Synthetic media validates the pipeline,
not semantic retrieval quality. Uses only disposable PostgreSQL schema and sources.
"""

# ruff: noqa: F811
import base64
import hashlib
import io
import os
import uuid

import pytest
from PIL import Image

from app.core.config import settings
from app.models.identity import Membership
from app.modules.generation import media_engine
from app.modules.library import processing_worker, worker
from app.modules.library.providers.wise import WiseClient
from tests.test_media_workflow import post_ok
from tests.test_media_workflow import studio as media_studio  # noqa: F401
from tests.test_wise_audio_live import audio_video  # noqa: F401
from tests.test_wise_live import live_settings, wait_local, wait_remote  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("INSPIRATION_WISE_MULTIMODAL_E2E") != "1",
    reason="Standalone media live acceptance requires INSPIRATION_WISE_MULTIMODAL_E2E=1",
)


def upload_media(client, base, content, filename):
    version = post_ok(
        client,
        f"{base}/uploads",
        {
            "filename": filename,
            "size_bytes": len(content),
            "fingerprint": hashlib.sha256(content).hexdigest(),
        },
    )
    for offset in range(0, len(content), version["chunk_size"]):
        response = client.post(
            f"{base}/versions/{version['id']}/chunk?offset={offset}",
            files={"file": ("chunk", content[offset : offset + version["chunk_size"]])},
        )
        assert response.status_code == 200, response.text
    post_ok(client, f"{base}/versions/{version['id']}/complete")
    worker.process(uuid.UUID(version["id"]))
    actual = client.get(f"{base}/versions/{version['id']}").json()
    assert actual["status"] == "ready", actual
    return actual


def test_real_standalone_images_and_audio_reference_retrieve_audio_and_video(
    media_studio,
    audio_video,
    monkeypatch,
    tmp_path,
):
    client, sessions, ids = media_studio
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "audio"])
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    picture = io.BytesIO()
    Image.new("RGB", (360, 240), "royalblue").save(picture, format="PNG")
    wav = tmp_path / "tone.wav"
    media_engine.run(
        ["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=8.2", str(wav)]
    )
    remote = WiseClient(settings.library_wise_url, settings.library_wise_token)
    tracked = []
    try:
        versions = []
        for pid, content, filename, cap in [
            (ids["project"], picture.getvalue(), "blue.png", "visual"),
            (ids["other"], wav.read_bytes(), "tone.wav", "audio"),
            (ids["other"], audio_video, "tone.mp4", "audio"),
        ]:
            base = f"/api/v1/projects/{pid}/library"
            version = upload_media(client, base, content, filename)
            run = post_ok(
                client,
                f"{base}/versions/{version['id']}/process",
                {"provider": "wise", "capabilities": [cap]},
            )
            tracked.append((pid, version["id"], run["id"]))
            completed = wait_local(client, base, run["id"], 600)
            assert completed["capability_status"][cap]["vector_count"] > 0
            versions.append(version)
        image, audio, video = versions
        image_base = f"/api/v1/projects/{ids['project']}/library"
        for payload in [
            {"query": "blue picture"},
            {"image": "data:image/png;base64," + base64.b64encode(picture.getvalue()).decode()},
        ]:
            result = post_ok(client, f"{image_base}/search", {"engine": "wise", **payload})
            assert result["items"] and result["coverage"]["indexed"] == 1
            for hit in result["items"]:
                assert hit["version_id"] == image["id"] and hit["media_type"] == "image"
                assert hit["start_ms"] is None and hit["end_ms"] is None
                assert hit["match_type"] == "image" and hit["matched_at_ms"] is None
        sound_base = f"/api/v1/projects/{ids['other']}/library"
        audio_query = {
            "engine": "wise",
            "target": "audio",
            "audio": "data:audio/wav;base64," + base64.b64encode(wav.read_bytes()).decode(),
            "limit": 100,
        }
        result = post_ok(client, f"{sound_base}/search", audio_query)
        assert result["coverage"]["indexed"] == 2
        assert {hit["version_id"] for hit in result["items"]} == {audio["id"], video["id"]}
        for hit in result["items"]:
            assert hit["match_type"] == "interval" and hit["media_type"] in ("video", "audio")
            assert 0 <= hit["start_ms"] < hit["end_ms"] <= 8300
        assert post_ok(client, f"{image_base}/search", audio_query)["items"] == []
        assert (
            remote.search(
                ids["other"], {"query": "tone", "modality": "audio", "source_ids": [image["id"]]}
            )["items"]
            == []
        )
        post_ok(client, f"{sound_base}/{audio['media_id']}/trash")
        assert {
            hit["version_id"]
            for hit in post_ok(client, f"{sound_base}/search", audio_query)["items"]
        } == {video["id"]}
        cleanup = next(
            row
            for row in client.get(f"{sound_base}/processing/runs").json()["items"]
            if row["operation"] == "delete"
        )
        wait_local(client, sound_base, cleanup["id"], 600)
    finally:
        for pid, source_id, anchor in tracked:
            job = remote.delete(pid, source_id, uuid.uuid4(), source_job_id=anchor)
            assert wait_remote(remote, job["job_id"], 600)["status"] == "ready"
