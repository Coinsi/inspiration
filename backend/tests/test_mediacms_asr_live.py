"""Explicitly enabled real Whisper -> annotations -> timestamped search test.

Requires INSPIRATION_MEDIACMS_ASR_E2E=1, the preview test's explicit URL/token
environment variables, TEST_DATABASE_URL, E2E_SPEECH_VIDEO and
E2E_SPEECH_EXPECTED. Only a caller-created short synthetic TTS fixture under
.runtime/media-integration is accepted. No user library file is discovered or
uploaded. The upstream copy is removed after its active step has stopped.
"""

# ruff: noqa: F811
import hashlib
import os
import time
import unicodedata
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.library import MediaVersion
from app.models.media_index import MediaAnnotation
from app.models.media_processing import MediaProcessingRun
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.generation.video_tools import probe_file
from app.modules.library import processing_mediacms, processing_worker
from app.modules.library.media_ingest import probe_media
from app.modules.library.providers.mediacms import MediaCMSClient, MediaCMSError, token_for_upload
from app.storage import cas
from tests.test_library import library, media_studio, upload  # noqa: F401
from tests.test_media_workflow import post_ok
from tests.test_wise_multimodal_live import upload_media

pytestmark = pytest.mark.skipif(
    os.getenv("INSPIRATION_MEDIACMS_ASR_E2E") != "1"
    or not all(
        os.getenv(key)
        for key in (
            "INSPIRATION_MEDIACMS_E2E_URL",
            "INSPIRATION_MEDIACMS_E2E_TOKEN",
            "E2E_SPEECH_VIDEO",
            "E2E_SPEECH_EXPECTED",
        )
    ),
    reason="Explicit ASR E2E opt-in, URL/token and synthetic speech fixture are required",
)


@pytest.fixture(autouse=True)
def no_background_processing(monkeypatch):
    monkeypatch.setattr(settings, "library_processing_enabled", False)
    monkeypatch.setattr(settings, "library_processing_auto", False)


def normalized(text):
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum()
    )


@pytest.mark.parametrize("media_kind", ["video", "audio"])
def test_live_speech_is_indexed_as_timestamped_annotations(
    library, tmp_path, monkeypatch, media_kind
):
    fixture = Path(os.environ["E2E_SPEECH_VIDEO"]).resolve(strict=True)
    fixture_root = (Path(__file__).resolve().parents[2] / ".runtime/media-integration").resolve()
    assert fixture.is_relative_to(fixture_root), (
        "Only the explicit synthetic fixture folder is allowed"
    )
    assert fixture.is_file() and fixture.stat().st_size <= 8 * 1024 * 1024
    info = probe_file(fixture, max_duration_ms=30000)
    assert info["has_video"] and info["has_audio"]
    expected = os.environ["E2E_SPEECH_EXPECTED"].strip()
    assert normalized(expected), "A nonempty expected spoken phrase is required"
    source = fixture.read_bytes()
    if media_kind == "audio":
        audio_source = tmp_path / "speech.wav"
        media_engine.run(
            ["-i", str(fixture), "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", str(audio_source)]
        )
        source = audio_source.read_bytes()
    client, sessions, ids, _ = library
    base = f"/api/v1/projects/{ids['project']}/library"
    version = (
        upload(client, base, source)
        if media_kind == "video"
        else upload_media(client, base, source, "speech.wav")
    )
    assert version["status"] == "ready"
    original_hash = hashlib.sha256(source).hexdigest()
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_mediacms_transcription_enabled", True)
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
        {
            "provider": "mediacms",
            "capabilities": ["speech"] if media_kind == "video" else ["preview", "speech"],
        },
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
            pytest.fail(f"Real ASR did not finish within 180 seconds: {status}; {phase}")
        with sessions() as db:
            run = db.get(MediaProcessingRun, run_id)
            actual = db.get(MediaVersion, uuid.UUID(version["id"]))
            assert run.outputs["transcript_hash"] and run.outputs["upstream_removed"]
            assert run.outputs["encoding_completion"]["complete"]
            assert actual.original_hash == original_hash
            assert cas.read_bytes(db.get(Blob, original_hash)) == source
            if media_kind == "audio":
                assert run.outputs["proxy_hash"] == actual.proxy_hash
                proxy = db.get(Blob, actual.proxy_hash)
                assert proxy.mime == "audio/mp4" and actual.poster_hash is None
                with cas.local_file(proxy) as path:
                    audio_info = probe_media(path, "audio")
                    assert abs(audio_info["duration_ms"] - actual.duration_ms) < 1500
            rows = list(
                db.scalars(
                    select(MediaAnnotation)
                    .where(
                        MediaAnnotation.version_id == actual.id,
                        MediaAnnotation.kind == "speech",
                    )
                    .order_by(MediaAnnotation.start_ms)
                )
            )
            assert rows, "Whisper returned no speech cues for the synthetic spoken fixture"
            assert all(row.source.get("provider") == "mediacms" for row in rows)
            assert all(0 <= row.start_ms < row.end_ms <= actual.duration_ms for row in rows)
            transcript = " ".join(row.text for row in rows)
            assert normalized(expected) in normalized(transcript), (
                f"Actual transcript: {transcript}"
            )
        found = post_ok(
            client,
            f"{base}/search",
            {
                "query": expected,
                "mode": "annotated",
                "kind": "speech",
            },
        )
        assert found["total"] >= 1, f"Real transcript was not retrieved: {transcript}"
        assert all(item["kind"] == "speech" for item in found["items"])
        assert all(item["start_ms"] < item["end_ms"] for item in found["items"])
        with actual_service() as remote:
            assert remote.get_media(remote_token, missing=True) is None
    finally:
        deadline = time.monotonic() + 180
        removed = False
        while time.monotonic() < deadline:
            try:
                with actual_service() as remote:
                    removed = processing_mediacms.poll_cleanup(remote, remote_token)
                if removed:
                    break
            except MediaCMSError:
                pass
            time.sleep(1)
        assert removed, f"ASR cleanup still waiting for this fixture's remote step: {remote_token}"
