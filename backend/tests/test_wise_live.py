"""Opt-in REAL WISE pipeline acceptance; not a retrieval-quality benchmark.

Requires INSPIRATION_WISE_E2E=1, INSPIRATION_WISE_URL,
INSPIRATION_WISE_TOKEN and TEST_DATABASE_URL. The latter creates a disposable
PostgreSQL schema. A running pinned bridge with preinstalled visual weights is
required; this test never downloads models or substitutes mock embeddings.

Real FFmpeg video -> library/proxy -> bridge upload -> WISE index -> query ->
version/time mapping -> project/whitelist isolation -> deletion are exercised.
Only configuration and database-session injection are replaced for isolation.
"""

# ruff: noqa: F811
import base64
import json
import math
import os
import time
import uuid
import warnings

import pytest

from app.core.config import settings
from app.core.errors import ProviderError
from app.models.identity import Membership
from app.models.media_processing import MediaProcessingRun
from app.models.storage import Blob
from app.modules.generation import media_engine, video_tools
from app.modules.library import processing_worker, worker
from app.modules.library.providers.wise import WiseClient
from app.storage import cas
from tests.test_library import upload
from tests.test_media_workflow import changing_video, post_ok  # noqa: F401
from tests.test_media_workflow import studio as media_studio  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("INSPIRATION_WISE_E2E") != "1",
    reason="Real WISE acceptance is opt-in: INSPIRATION_WISE_E2E=1",
)


@pytest.fixture(autouse=True)
def live_settings(monkeypatch, tmp_path):
    required = ("INSPIRATION_WISE_URL", "INSPIRATION_WISE_TOKEN", "TEST_DATABASE_URL")
    missing = [name for name in required if not os.environ.get(name)]
    assert not missing, "Live WISE test requires environment: " + ", ".join(missing)
    # Keep application background workers away from both production and test DBs.
    # The real worker function is advanced explicitly below.
    monkeypatch.setattr(settings, "library_processing_enabled", False)
    monkeypatch.setattr(settings, "library_processing_auto", False)
    monkeypatch.setattr(settings, "library_wise_url", os.environ["INSPIRATION_WISE_URL"])
    monkeypatch.setattr(settings, "library_wise_token", os.environ["INSPIRATION_WISE_TOKEN"])
    monkeypatch.setattr(settings, "library_processing_temp_dir", str(tmp_path / "transfer"))
    monkeypatch.setattr(settings, "library_staging_dir", str(tmp_path / "staging"))


def wait_local(client, base, job_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processing_worker.process(uuid.UUID(job_id))
        response = client.get(f"{base}/processing/runs?limit=100")
        assert response.status_code == 200, response.text
        run = next(item for item in response.json()["items"] if item["id"] == job_id)
        if run["status"] in ("ready", "failed", "canceled"):
            assert run["status"] == "ready", run
            return run
        time.sleep(2)
    pytest.fail("Real WISE processing exceeded the configured acceptance deadline")


def wait_remote(remote, job_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = remote.get_job(job_id)
        assert result is not None, "Accepted WISE job disappeared"
        if result["status"] in ("ready", "failed", "canceled"):
            return result
        time.sleep(2)
    raise AssertionError("Remote WISE cleanup exceeded the acceptance deadline")


@pytest.fixture
def wise_video(changing_video, tmp_path):
    # Six seconds also permits a real >4s CLAP chunk when the optional audio
    # integration is enabled, while preserving the red/blue reference frame.
    source, extended = tmp_path / "input.mp4", tmp_path / "extended.mp4"
    source.write_bytes(changing_video)
    media_engine.run(
        ["-stream_loop", "2", "-i", str(source), "-t", "6", "-c", "copy", str(extended)]
    )
    return extended.read_bytes()


def test_real_wise_video_index_query_scope_and_delete(
    media_studio,
    wise_video,
    monkeypatch,
    record_property,
):
    client, sessions, ids = media_studio
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    remote = WiseClient(settings.library_wise_url, settings.library_wise_token)
    health = remote.health()
    assert health.get("worker_alive") is True, health
    assert health.get("capabilities", {}).get("visual", {}).get("configured") is True
    timeout = int(os.environ.get("INSPIRATION_WISE_TIMEOUT_SECONDS", "600"))
    assert 30 <= timeout <= 3600
    tracked = []
    anchors = {}
    succeeded = False
    try:
        base = f"/api/v1/projects/{ids['project']}/library"
        other_base = f"/api/v1/projects/{ids['other']}/library"
        query = {
            "engine": "wise",
            "target": "visual",
            "query": "蓝色画面",
            "limit": 20,
            "collapse_versions": False,
        }
        assert client.post(f"{other_base}/search", json=query).status_code == 403
        with sessions() as db:
            db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
            db.commit()
        versions = []
        for project_id, route in [(ids["project"], base), (ids["other"], other_base)]:
            version = upload(client, route, wise_video)
            assert version["status"] == "ready" and version["proxy_hash"]
            tracked.append((project_id, version["id"]))
            run = post_ok(
                client,
                f"{route}/versions/{version['id']}/process",
                {
                    "provider": "wise",
                    "capabilities": ["visual"],
                },
            )
            anchors[version["id"]] = [run["id"]]
            completed = wait_local(client, route, run["id"], timeout)
            visual = completed["capability_status"]["visual"]
            assert visual["state"] == "ready" and visual["vector_count"] > 0
            assert remote.health().get("upstream_installation_verified") is True
            versions.append(version)
        first, other = versions
        started = time.monotonic()
        result = post_ok(client, f"{base}/search", query)
        cold_seconds = time.monotonic() - started
        assert result["coverage"]["indexed"] == 1
        assert result["items"], "A valid indexed clip must return at least one real visual hit"
        for hit in result["items"]:
            assert hit["version_id"] == first["id"]
            assert 0 <= hit["start_ms"] < hit["end_ms"] <= first["duration_ms"]
            assert math.isfinite(hit["score"])
            assert hit["match_type"] in ("frame", "interval")
        # Preserve observed ordering for review; no unsupported assertion that a
        # synthetic color clip proves Chinese semantics or production recall.
        record_property(
            "wise_query_observations",
            json.dumps(
                {
                    "query": query["query"],
                    "items": result["items"],
                },
                ensure_ascii=False,
            ),
        )
        started = time.monotonic()
        repeated = post_ok(client, f"{base}/search", query)
        warm_seconds = time.monotonic() - started
        assert repeated["items"] == result["items"]
        record_property(
            "wise_query_seconds",
            json.dumps({"first": cold_seconds, "repeat": warm_seconds}),
        )
        # Extract a genuine reference frame and exercise the full image-upload
        # search path through our API, the bridge and SigLIP2's image encoder.
        frame, output_type, _ = video_tools.process(
            wise_video, {"operation": "frames", "times_ms": [1200]}
        )[0]
        assert output_type == "image" and frame.startswith(b"\x89PNG")
        image_query = {
            **query,
            "query": "",
            "image": "data:image/png;base64," + base64.b64encode(frame).decode(),
        }
        image_result = post_ok(client, f"{base}/search", image_query)
        assert image_result["items"], "A reference frame must return real visual hits"
        for hit in image_result["items"]:
            assert hit["version_id"] == first["id"]
            assert 0 <= hit["start_ms"] < hit["end_ms"] <= first["duration_ms"]
            assert math.isfinite(hit["score"])
            assert hit["match_type"] == "frame"
        record_property("wise_image_query_observations", json.dumps(image_result["items"]))
        scoped = remote.search(
            ids["project"],
            {
                "query": "blue",
                "modality": "visual",
                "limit": 20,
                "source_ids": [first["id"], other["id"]],
            },
        )
        assert scoped["items"]
        assert {item["source_id"] for item in scoped["items"]} == {first["id"]}
        assert (
            remote.search(
                ids["project"],
                {
                    "query": "blue",
                    "modality": "visual",
                    "limit": 20,
                    "source_ids": [other["id"]],
                },
            )["items"]
            == []
        )
        assert (
            remote.search(
                ids["other"],
                {
                    "query": "blue",
                    "modality": "visual",
                    "limit": 20,
                    "source_ids": [first["id"]],
                },
            )["items"]
            == []
        )
        post_ok(client, f"{base}/{first['media_id']}/trash")
        assert post_ok(client, f"{base}/search", query)["items"] == []
        cleanup_runs = client.get(f"{base}/processing/runs?limit=100").json()["items"]
        cleanup = next(item for item in cleanup_runs if item["operation"] == "delete")
        wait_local(client, base, cleanup["id"], timeout)
        assert (
            remote.search(
                ids["project"],
                {
                    "query": "blue",
                    "modality": "visual",
                    "source_ids": [first["id"]],
                },
            )["items"]
            == []
        )
        # Restoring the library item alone must not resurrect its deleted index.
        original_job = anchors[first["id"]][0]
        post_ok(client, f"{base}/{first['media_id']}/restore")
        assert post_ok(client, f"{base}/search", query)["items"] == []
        restored_run = post_ok(
            client,
            f"{base}/versions/{first['id']}/process",
            {"provider": "wise", "capabilities": ["visual"]},
        )
        anchors[first["id"]].append(restored_run["id"])
        wait_local(client, base, restored_run["id"], timeout)
        assert remote.get_job(restored_run["id"])["generation"] == 1
        assert post_ok(client, f"{base}/search", query)["items"]
        # Known old retries are idempotent; an unseen delayed deletion carrying
        # the previous source generation is rejected instead of deleting v1.
        replay = remote.delete(
            ids["project"], first["id"], cleanup["id"], source_job_id=original_job
        )
        assert replay["status"] == "ready"
        with pytest.raises(ProviderError):
            remote.delete(ids["project"], first["id"], uuid.uuid4(), source_job_id=original_job)
        # Reuse the exact immutable proxy bytes: omission of a restore marker
        # must itself reject a late first-generation upload, not a hash mismatch.
        with sessions() as db:
            original = db.get(MediaProcessingRun, uuid.UUID(original_job))
            blob = db.get(Blob, original.source_hash)
            with cas.local_file(blob) as proxy_path:
                with pytest.raises(ProviderError):
                    remote.submit(ids["project"], first["id"], uuid.uuid4(), proxy_path, ["visual"])
        assert post_ok(client, f"{base}/search", query)["items"]
        if os.environ.get("INSPIRATION_WISE_AUDIO_E2E") == "1":
            monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "audio"])
            audio_run = post_ok(
                client,
                f"{base}/versions/{first['id']}/process",
                {"provider": "wise", "capabilities": ["audio"]},
            )
            anchors[first["id"]].append(audio_run["id"])
            completed_audio = wait_local(client, base, audio_run["id"], timeout)
            assert completed_audio["capability_status"]["audio"]["vector_count"] > 0
            assert remote.get_job(audio_run["id"])["generation"] == 1
            assert post_ok(client, f"{base}/search", query)["items"]
            sound_query = {**query, "target": "audio", "query": "A continuous electronic tone"}
            assert post_ok(client, f"{base}/search", sound_query)["items"]
        post_ok(client, f"{base}/{first['media_id']}/trash")
        assert post_ok(client, f"{base}/search", query)["items"] == []
        restored_cleanup = next(
            item
            for item in client.get(f"{base}/processing/runs?limit=100").json()["items"]
            if item["operation"] == "delete" and item["id"] != cleanup["id"]
        )
        wait_local(client, base, restored_cleanup["id"], timeout)
        succeeded = True
    finally:
        cleanup_errors = []
        for project_id, source_id in tracked:
            try:
                known_anchor = None
                for candidate in reversed(anchors.get(source_id, [])):
                    if remote.get_job(candidate):
                        known_anchor = candidate
                        break
                cleanup = remote.delete(
                    project_id, source_id, uuid.uuid4(), source_job_id=known_anchor
                )
                outcome = wait_remote(remote, cleanup["job_id"], timeout)
                assert outcome["status"] == "ready", "WISE cleanup did not finish"
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        if cleanup_errors:
            message = "Live WISE source cleanup failed: " + ", ".join(cleanup_errors)
            if succeeded:
                pytest.fail(message)
            warnings.warn(message, RuntimeWarning, stacklevel=1)
