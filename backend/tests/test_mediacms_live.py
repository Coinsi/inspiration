"""Opt-in deployed MediaCMS acceptance test; no simulated HTTP responses.

Set INSPIRATION_MEDIACMS_E2E=1, INSPIRATION_MEDIACMS_E2E_URL,
INSPIRATION_MEDIACMS_E2E_TOKEN, and TEST_DATABASE_URL explicitly. Credentials
are never loaded from project files or printed by this test. The PostgreSQL
fixture creates and drops a random schema; the remote service receives only a
new synthetic video owned by this run. Its deterministic token is cleaned in
finally, including on assertion failures.
"""

# ruff: noqa: F811
import hashlib
import os
import time
import uuid

import httpx
import pytest
from PIL import Image

from app.core.config import settings
from app.models.library import MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.generation.video_tools import probe_file
from app.modules.library import processing_mediacms, processing_worker
from app.modules.library.providers.mediacms import MediaCMSClient, MediaCMSError, token_for_upload
from app.storage import cas
from tests.test_library import library, media_studio, upload  # noqa: F401
from tests.test_media_workflow import post_ok

pytestmark = pytest.mark.skipif(
    os.getenv("INSPIRATION_MEDIACMS_E2E") != "1"
    or not os.getenv("INSPIRATION_MEDIACMS_E2E_URL")
    or not os.getenv("INSPIRATION_MEDIACMS_E2E_TOKEN"),
    reason="Explicit MediaCMS E2E opt-in, URL and token are required",
)


@pytest.fixture(autouse=True)
def no_background_processing(monkeypatch):
    # The test drives exactly its new run, never the normal worker queue.
    monkeypatch.setattr(settings, "library_processing_enabled", False)
    monkeypatch.setattr(settings, "library_processing_auto", False)


def test_live_preview_is_imported_into_cas_and_temporary_copy_is_deleted(
    library, tmp_path, monkeypatch
):
    client, sessions, ids, source = library
    base = f"/api/v1/projects/{ids['project']}/library"
    version = upload(client, base, source)
    assert version["status"] == "ready"
    original_hash = hashlib.sha256(source).hexdigest()
    assert version["original_hash"] == original_hash
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_processing_temp_dir", str(tmp_path / "processing"))
    monkeypatch.setattr(settings, "library_processing_reserve_bytes", 0)
    monkeypatch.setattr(settings, "library_processing_max_transfer_bytes", 32 * 1024 * 1024)
    monkeypatch.setattr(
        settings, "library_mediacms_url", os.environ["INSPIRATION_MEDIACMS_E2E_URL"]
    )
    monkeypatch.setattr(
        settings, "library_mediacms_token", os.environ["INSPIRATION_MEDIACMS_E2E_TOKEN"]
    )
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)

    def actual_service():
        return MediaCMSClient(
            settings.library_mediacms_url,
            settings.library_mediacms_token,
            private_workflow_confirmed=True,
            timeout=httpx.Timeout(connect=5, read=15, write=30, pool=5),
        )

    monkeypatch.setattr(processing_mediacms, "client", actual_service)
    submitted = post_ok(
        client,
        f"{base}/versions/{version['id']}/process",
        {"provider": "mediacms", "capabilities": ["preview"]},
    )
    run_id = uuid.UUID(submitted["id"])
    remote_token = token_for_upload(str(run_id))
    with actual_service() as remote:
        assert remote.get_media(remote_token, missing=True) is None
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            processing_worker.process(run_id)
            with sessions() as db:
                run = db.get(MediaProcessingRun, run_id)
                status, phase, error = run.status, run.progress, run.error
            assert status not in ("failed", "canceled"), f"{status}: {phase}; {error}"
            if status == "ready":
                break
            time.sleep(1)
        else:
            pytest.fail(f"MediaCMS preview did not finish in 180 seconds: {status}; {phase}")

        with sessions() as db:
            run = db.get(MediaProcessingRun, run_id)
            actual = db.get(MediaVersion, uuid.UUID(version["id"]))
            assert run.outputs["import_complete"] and run.outputs["upstream_removed"]
            assert actual.original_hash == original_hash
            assert cas.read_bytes(db.get(Blob, original_hash)) == source
            assert actual.proxy_hash == run.outputs["proxy_hash"]
            assert actual.proxy_hash != original_hash
            proxy = db.get(Blob, actual.proxy_hash)
            assert proxy.mime == "video/mp4"
            with cas.local_file(proxy) as path:
                info = probe_file(path)
                assert info["has_video"]
                assert abs(info["duration_ms"] - version["duration_ms"]) <= 300
                assert "Video:" in media_engine.run(["-i", str(path), "-f", "null", "-"])
            assert actual.poster_hash == run.outputs["poster_hash"]
            poster = db.get(Blob, actual.poster_hash)
            with cas.local_file(poster) as path, Image.open(path) as image:
                image.verify()
            result = client.get(f"{base}/versions/{version['id']}")
            assert result.status_code == 200
            assert result.json()["proxy_hash"] == actual.proxy_hash
        with actual_service() as remote:
            assert remote.get_media(remote_token, missing=True) is None
    finally:
        # Only this test's UUID-derived identity is eligible for cleanup. Do not
        # enumerate/delete other service uploads or touch real library projects.
        for attempt in range(3):
            try:
                with actual_service() as remote:
                    remote.delete_media(remote_token)
                    assert remote.get_media(remote_token, missing=True) is None
                break
            except MediaCMSError:
                if attempt == 2:
                    pytest.fail(f"Could not remove this E2E's temporary media: {remote_token}")
                time.sleep(1)
