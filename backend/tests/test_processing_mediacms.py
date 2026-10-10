"""Real PostgreSQL/CAS/FFmpeg orchestration with only the remote HTTP service mocked.

TEST_DATABASE_URL enables isolated random-schema tests. No live project data or
external MediaCMS instance is mutated.
"""

# ruff: noqa: F811
import io
import uuid
from dataclasses import dataclass, field

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import settings
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaAnnotation
from app.models.media_processing import MediaProcessingRun
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.library import processing_mediacms, processing_worker
from app.modules.library.providers.mediacms import (
    MediaCMSCancelled,
    MediaCMSClient,
    token_for_upload,
)
from app.storage import cas
from tests.test_media_workflow import changing_video, studio  # noqa: F401


@pytest.fixture(autouse=True)
def no_background_processing(monkeypatch):
    monkeypatch.setattr(settings, "library_processing_enabled", False)


@dataclass
class Remote:
    token: str
    proxy: bytes
    poster: bytes
    calls: list = field(default_factory=list)
    exists: bool = False
    delete_failures: int = 0
    upload_cancel: bool = False
    upload_timeout: bool = False
    download_callback: object = None
    speech_status: str = "success"
    encoding_complete: bool = True
    encoding_failed: bool = False

    def handle(self, req):
        self.calls.append((req.method, req.url.path))
        if req.url.path == "/api/inspiration/v1/media/":
            assert req.method == "POST"
            if self.upload_cancel:
                raise MediaCMSCancelled("MediaCMS upload canceled")
            self.exists = True
            if self.upload_timeout:
                raise httpx.ReadTimeout("upload response lost", request=req)
            return httpx.Response(
                201,
                json={
                    "friendly_token": self.token,
                    "state": "private",
                    "encoding_status": "success",
                },
            )
        if req.url.path == "/api/v1/media/" + self.token:
            if req.method == "DELETE":
                if self.delete_failures:
                    self.delete_failures -= 1
                    raise httpx.ReadTimeout("delete response lost", request=req)
                self.exists = False
                return httpx.Response(204)
            if not self.exists:
                return httpx.Response(400, json={"detail": "media file does not exist"})
            return httpx.Response(200, json={"state": "private", "encoding_status": "success"})
        if req.url.path.endswith("/manifest"):
            return httpx.Response(
                200,
                json={
                    "remote_id": self.token,
                    "encoding_status": "success",
                    "processing": {
                        "dispatch": "submitted",
                        "complete": self.encoding_complete,
                        "status": "fail"
                        if self.encoding_failed
                        else "success"
                        if self.encoding_complete
                        else "running",
                        "expected": 2,
                        "terminal": 2 if self.encoding_complete else 1,
                        "pending": 0 if self.encoding_complete else 1,
                        "running": 0,
                        "failed": int(self.encoding_failed),
                    },
                    "subtitles": [],
                    "thumbnail": None,
                    "preview": None,
                    "poster": {
                        "key": "poster",
                        "url": self.output_url("poster"),
                        "mime": "image/jpeg",
                        "size_bytes": len(self.poster),
                    },
                    "encodings": [
                        {
                            "id": 1,
                            "key": "encoding-1",
                            "url": self.output_url("encoding-1"),
                            "mime": "video/mp4",
                            "size_bytes": len(self.proxy),
                            "resolution": 720,
                            "codec": "h264",
                        }
                    ],
                },
            )
        if "/files/" in req.url.path:
            if self.download_callback:
                self.download_callback()
            return httpx.Response(
                200, content=(self.poster if req.url.path.endswith("/poster") else self.proxy)
            )
        if req.url.path.endswith("/transcription"):
            return httpx.Response(
                200,
                json={
                    "status": self.speech_status,
                    "request_id": 7,
                    "cues": [{"start_ms": 0, "end_ms": 950, "text": "机器字幕", "language": "und"}],
                },
            )
        if req.url.path.endswith("/encode"):
            assert req.method == "POST"
            self.encoding_failed = False
            self.encoding_complete = False
            return httpx.Response(202, json={"status": "running"})
        raise AssertionError(f"Unexpected HTTP request: {req.method} {req.url.path}")

    def output_url(self, key):
        return f"/api/inspiration/v1/media/{self.token}/files/{key}"

    def count(self, method, suffix):
        return sum(m == method and path.endswith(suffix) for m, path in self.calls)


@pytest.fixture
def processing_case(studio, changing_video, tmp_path, monkeypatch):
    _, sessions, ids = studio
    monkeypatch.setattr(settings, "library_mediacms_transcription_enabled", True)
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "library_processing_temp_dir", str(tmp_path / "processing"))
    monkeypatch.setattr(settings, "library_processing_reserve_bytes", 0)
    monkeypatch.setattr(settings, "library_processing_max_transfer_bytes", 32 * 1024 * 1024)
    source_path, proxy_path = tmp_path / "source.mp4", tmp_path / "proxy.mp4"
    source_path.write_bytes(changing_video)
    media_engine.run(
        [
            "-i",
            str(source_path),
            "-vf",
            "scale=160:90",
            "-c:v",
            "libx264",
            "-crf",
            "30",
            "-c:a",
            "aac",
            "-threads",
            "1",
            str(proxy_path),
        ]
    )
    poster = io.BytesIO()
    Image.new("RGB", (160, 90), "navy").save(poster, format="JPEG")
    with sessions() as db:
        blob = cas.put_bytes(
            db, changing_video, "video/mp4", duration_ms=2000, width=320, height=180
        )
        media = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="Isolated test video")
        db.add(media)
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            ordinal=1,
            filename="source.mp4",
            fingerprint=blob.hash,
            size_bytes=len(changing_video),
            uploaded_bytes=len(changing_video),
            status="ready",
            original_hash=blob.hash,
            proxy_hash=blob.hash,
            duration_ms=2000,
            width=320,
            height=180,
        )
        db.add(version)
        db.flush()
        run = MediaProcessingRun(
            version_id=version.id,
            created_by=ids["user"],
            provider="mediacms",
            capabilities=["preview", "speech"],
            source_hash=blob.hash,
            status="queued",
        )
        db.add(run)
        db.flush()
        db.commit()
        data = {
            "run": run.id,
            "version": version.id,
            "original": blob.hash,
            "project": media.project_id,
            "user": ids["user"],
        }
    remote = Remote(token_for_upload(str(data["run"])), proxy_path.read_bytes(), poster.getvalue())
    monkeypatch.setattr(
        processing_mediacms,
        "client",
        lambda: MediaCMSClient(
            "https://media.test",
            "isolated-token",
            private_workflow_confirmed=True,
            client=httpx.Client(transport=httpx.MockTransport(remote.handle)),
        ),
    )
    return sessions, data, remote, changing_video


def test_actual_video_import_preserves_original_and_deduplicates_machine_subtitles(processing_case):
    sessions, ids, remote, source = processing_case
    with sessions() as db:
        db.add_all(
            [
                MediaAnnotation(
                    version_id=ids["version"],
                    created_by=ids["user"],
                    start_ms=0,
                    end_ms=1000,
                    text="手工字幕",
                    kind="speech",
                    source={},
                ),
                MediaAnnotation(
                    version_id=ids["version"],
                    created_by=ids["user"],
                    start_ms=0,
                    end_ms=1000,
                    text="旧机器字幕",
                    kind="speech",
                    source={"provider": "mediacms"},
                ),
                MediaAnnotation(
                    version_id=ids["version"],
                    created_by=ids["user"],
                    start_ms=0,
                    end_ms=1000,
                    text="导入字幕",
                    kind="speech",
                    source={"provider": "srt_import"},
                ),
            ]
        )
        db.commit()
    processing_worker.process(ids["run"])
    with sessions() as db:
        run, version = db.get(MediaProcessingRun, ids["run"]), db.get(MediaVersion, ids["version"])
        assert run.status == "ready", run.error
        assert run.outputs["import_complete"] and run.outputs["upstream_removed"]
        assert version.original_hash == ids["original"]
        assert cas.read_bytes(db.get(Blob, version.original_hash)) == source
        assert version.proxy_hash != version.original_hash
        assert cas.read_bytes(db.get(Blob, version.proxy_hash)) == remote.proxy
        assert db.get(Blob, version.proxy_hash).width == 160
        assert db.get(Blob, version.poster_hash).mime == "image/jpeg"
        # A repeated publication is idempotent and does not remove human/imported data.
        processing_mediacms.publish(db, run, version, ids["project"])
        db.commit()
        rows = list(
            db.scalars(
                select(MediaAnnotation).where(
                    MediaAnnotation.version_id == version.id,
                )
            )
        )
        assert sorted(row.text for row in rows) == sorted(["手工字幕", "导入字幕", "机器字幕"])
    assert remote.count("POST", "/api/inspiration/v1/media/") == 1
    assert remote.count("DELETE", remote.token) == 1


def test_delete_timeout_reuses_import_checkpoint_without_reupload_or_redownload(processing_case):
    sessions, ids, remote, _ = processing_case
    remote.delete_failures = 1
    processing_worker.process(ids["run"])
    with sessions() as db:
        run, version = db.get(MediaProcessingRun, ids["run"]), db.get(MediaVersion, ids["version"])
        assert run.status == "waiting"
        assert run.progress["phase"] == "reconciling"
        assert run.outputs["import_complete"]
        assert not run.outputs.get("upstream_removed")
        assert version.proxy_hash == ids["original"]  # not published before cleanup succeeds
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "ready", run.error
        assert run.outputs["upstream_removed"]
    assert remote.count("POST", "/api/inspiration/v1/media/") == 1
    assert remote.count("GET", "/files/encoding-1") == 1
    assert remote.count("GET", "/files/poster") == 1
    assert remote.count("DELETE", remote.token) == 2


def test_lost_upload_response_reconciles_identity_without_reupload(processing_case):
    sessions, ids, remote, _ = processing_case
    remote.upload_timeout = True
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "waiting"
        assert run.progress["phase"] == "reconciling"
        assert not run.outputs.get("import_complete")
        assert db.get(MediaVersion, ids["version"]).proxy_hash == ids["original"]
    assert remote.exists
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "ready", run.error
        assert run.outputs["upstream_removed"]
    assert remote.count("POST", "/api/inspiration/v1/media/") == 1
    assert remote.count("DELETE", remote.token) == 1


def test_cancel_during_transfer_never_publishes(processing_case):
    sessions, ids, remote, _ = processing_case

    def request_cancel():
        with sessions() as db:
            run = db.get(MediaProcessingRun, ids["run"])
            run.cancel_requested = True
            db.commit()

    remote.download_callback = request_cancel
    processing_worker.process(ids["run"])
    with sessions() as db:
        run, version = db.get(MediaProcessingRun, ids["run"]), db.get(MediaVersion, ids["version"])
        assert run.cancel_requested
        assert run.status == "waiting"
        assert version.proxy_hash == ids["original"]
        assert not list(
            db.scalars(
                select(MediaAnnotation).where(
                    MediaAnnotation.version_id == ids["version"],
                )
            )
        )
    processing_worker.process(ids["run"])
    with sessions() as db:
        assert db.get(MediaProcessingRun, ids["run"]).status == "canceled"
        assert db.get(MediaVersion, ids["version"]).proxy_hash == ids["original"]


def test_upload_cancellation_stays_resumable_instead_of_becoming_failure(processing_case):
    sessions, ids, remote, _ = processing_case
    remote.upload_cancel = True
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "waiting"
        assert run.progress["phase"] == "interrupted"
        assert db.get(MediaVersion, ids["version"]).proxy_hash == ids["original"]
    remote.upload_cancel = False
    processing_worker.process(ids["run"])
    with sessions() as db:
        assert db.get(MediaProcessingRun, ids["run"]).status == "ready"


def test_invalid_download_is_not_published(processing_case):
    sessions, ids, remote, _ = processing_case
    remote.proxy = b"This is not a video"
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "failed"
        assert not run.outputs.get("proxy_hash")
        assert db.get(MediaVersion, ids["version"]).proxy_hash == ids["original"]
    assert remote.count("DELETE", remote.token) == 0


def test_first_ready_rendition_does_not_delete_other_pending_profiles(processing_case):
    sessions, ids, remote, _ = processing_case
    remote.encoding_complete = False
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "waiting"
        assert run.progress["phase"] == "transcoding"
        assert not run.outputs.get("import_complete")
    assert remote.count("DELETE", remote.token) == 0
    assert remote.count("GET", "/files/encoding-1") == 0
    remote.encoding_complete = True
    processing_worker.process(ids["run"])
    with sessions() as db:
        assert db.get(MediaProcessingRun, ids["run"]).status == "ready"
    assert remote.count("POST", "/api/inspiration/v1/media/") == 1
    assert remote.count("DELETE", remote.token) == 1


def test_failed_secondary_profile_never_claims_success(processing_case):
    sessions, ids, remote, _ = processing_case
    remote.encoding_complete = False
    processing_worker.process(ids["run"])
    remote.encoding_complete = True
    remote.encoding_failed = True
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "failed"
        assert not run.outputs.get("import_complete")
        assert db.get(MediaVersion, ids["version"]).proxy_hash == ids["original"]
    assert remote.count("DELETE", remote.token) == 0


def test_failed_profile_retry_uses_bridge_barrier_until_every_profile_finishes(processing_case):
    sessions, ids, remote, _ = processing_case
    remote.encoding_complete = False
    processing_worker.process(ids["run"])
    remote.encoding_complete = True
    remote.encoding_failed = True
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "failed"
        run.status = "queued"  # state transition performed by the existing retry API
        db.commit()
    processing_worker.process(ids["run"])
    assert remote.count("POST", "/encode") == 1
    assert remote.count("POST", remote.token) == 0
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "waiting"
    assert remote.count("DELETE", remote.token) == 0
    remote.encoding_complete = True
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "ready"
        assert run.outputs["encoding_completion"]["terminal"] == 2
    assert remote.count("POST", "/api/inspiration/v1/media/") == 1


@pytest.mark.parametrize("running_step", ["transcoding", "transcribing"])
def test_cancel_waits_for_running_remote_step_before_deleting_source(processing_case, running_step):
    sessions, ids, remote, _ = processing_case
    remote.encoding_complete = running_step != "transcoding"
    remote.speech_status = "running" if running_step == "transcribing" else "not_requested"
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        run.cancel_requested = True
        db.commit()
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "waiting" and run.progress["phase"] == "canceling"
    assert remote.count("DELETE", remote.token) == 0
    assert remote.count("POST", "/transcription") == 0
    remote.encoding_complete = True
    remote.speech_status = "success"
    processing_worker.process(ids["run"])
    with sessions() as db:
        run = db.get(MediaProcessingRun, ids["run"])
        assert run.status == "canceled"
        assert db.get(MediaVersion, ids["version"]).proxy_hash == ids["original"]
    assert remote.count("DELETE", remote.token) == 1
