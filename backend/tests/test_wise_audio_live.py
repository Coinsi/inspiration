"""Optional real CLAP audio retrieval; no mocks and no model download.

INSPIRATION_WISE_AUDIO_E2E=1 plus INSPIRATION_WISE_URL,
INSPIRATION_WISE_TOKEN and TEST_DATABASE_URL explicitly enable this test.
Prepare both locked audio model repositories first, then enable the bridge's
audio capability. Uses an isolated PostgreSQL schema and >4s real video/audio.
Synthetic tone matching validates integration only, not acoustic semantic quality.
"""

# ruff: noqa: F811
import json
import math
import os
import uuid
import warnings

import pytest

from app.core.config import settings
from app.modules.generation import media_engine
from app.modules.library import processing_worker, worker
from app.modules.library.providers.wise import WiseClient
from tests.test_library import upload
from tests.test_media_workflow import post_ok
from tests.test_media_workflow import studio as media_studio  # noqa: F401
from tests.test_wise_live import live_settings, wait_local, wait_remote  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("INSPIRATION_WISE_AUDIO_E2E") != "1",
    reason="Real CLAP acceptance is opt-in: INSPIRATION_WISE_AUDIO_E2E=1",
)


@pytest.fixture
def audio_video(tmp_path):
    path = tmp_path / "audio-tone.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x180:r=8:d=8.2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=8.2",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-threads",
            "2",
            "-shortest",
            str(path),
        ]
    )
    return path.read_bytes()


def test_real_clap_video_audio_index_query_and_cleanup(
    media_studio,
    audio_video,
    monkeypatch,
    record_property,
):
    client, sessions, ids = media_studio
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_wise_capabilities", ["audio"])
    remote = WiseClient(settings.library_wise_url, settings.library_wise_token)
    health = remote.health()
    assert health.get("worker_alive") is True, health
    assert health.get("capabilities", {}).get("audio", {}).get("configured") is True
    timeout = int(os.environ.get("INSPIRATION_WISE_TIMEOUT_SECONDS", "600"))
    assert 30 <= timeout <= 3600
    base = f"/api/v1/projects/{ids['project']}/library"
    version = None
    succeeded = False
    try:
        version = upload(client, base, audio_video)
        assert version["status"] == "ready" and version["duration_ms"] > 4000
        run = post_ok(
            client,
            f"{base}/versions/{version['id']}/process",
            {
                "provider": "wise",
                "capabilities": ["audio"],
            },
        )
        completed = wait_local(client, base, run["id"], timeout)
        audio = completed["capability_status"]["audio"]
        assert audio["state"] == "ready" and audio["vector_count"] > 0
        assert remote.health().get("upstream_installation_verified") is True
        query = {
            "engine": "wise",
            "target": "audio",
            "query": "A continuous high pitched electronic tone",
            "limit": 20,
            "collapse_versions": False,
        }
        result = post_ok(client, f"{base}/search", query)
        assert result["coverage"]["indexed"] == 1
        assert result["items"], "Indexed audio must produce real CLAP matches"
        for hit in result["items"]:
            assert hit["version_id"] == version["id"]
            assert hit["kind"] == "audio" and hit["match_type"] == "interval"
            assert 0 <= hit["start_ms"] < hit["end_ms"] <= version["duration_ms"]
            assert math.isfinite(hit["score"])
        record_property(
            "clap_pipeline_observations",
            json.dumps(
                {
                    "query": query["query"],
                    "index_vectors": audio["vector_count"],
                    "items": result["items"],
                    "quality_benchmark": False,
                }
            ),
        )
        foreign_id = str(uuid.uuid4())
        assert (
            remote.search(
                ids["project"],
                {
                    "query": query["query"],
                    "modality": "audio",
                    "source_ids": [foreign_id],
                },
            )["items"]
            == []
        )
        post_ok(client, f"{base}/{version['media_id']}/trash")
        assert post_ok(client, f"{base}/search", query)["items"] == []
        runs = client.get(f"{base}/processing/runs?limit=100").json()["items"]
        cleanup = next(item for item in runs if item["operation"] == "delete")
        wait_local(client, base, cleanup["id"], timeout)
        assert (
            remote.search(
                ids["project"],
                {
                    "query": query["query"],
                    "modality": "audio",
                    "source_ids": [version["id"]],
                },
            )["items"]
            == []
        )
        succeeded = True
    finally:
        if version:
            try:
                cleanup = remote.delete(ids["project"], version["id"], uuid.uuid4())
                outcome = wait_remote(remote, cleanup["job_id"], timeout)
                assert outcome["status"] == "ready"
            except Exception as exc:
                message = "Live CLAP cleanup failed: " + type(exc).__name__
                if succeeded:
                    pytest.fail(message)
                warnings.warn(message, RuntimeWarning, stacklevel=1)
