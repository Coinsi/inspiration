"""Opt-in real automatic admission -> WISE/MediaCMS -> search acceptance.

Default skip. Set INSPIRATION_MEDIA_AUTO_E2E=1, TEST_DATABASE_URL,
INSPIRATION_WISE_URL/TOKEN, INSPIRATION_MEDIACMS_E2E_URL/TOKEN,
E2E_SPEECH_VIDEO and E2E_SPEECH_EXPECTED. The explicit short synthetic speech
fixture must be under .runtime/media-integration. Reserve an idle service window
before running; the existing benchmark lock is held until cleanup finishes.

Run from backend: .venv/Scripts/python.exe -m pytest
tests/test_media_automatic_live.py -q. No service/config edits or model downloads.
Real queue run_once functions are advanced deterministically, not a production
daemon. There is no manual process API call, fake provider or fake embedding.
Historical failed/canceled rows are explicit DB setup, NOT real remote faults.
"""

# ruff: noqa: F811
import hashlib
import io
import json
import os
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import Settings, settings
from app.models.library import MediaVersion
from app.models.media_index import MediaAnnotation
from app.models.media_processing import MediaProcessingRun
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.generation.video_tools import probe_file
from app.modules.library import processing_mediacms, processing_worker, worker
from app.modules.library.providers.mediacms import token_for_upload
from app.modules.library.providers.wise import WiseClient
from app.storage import cas
from scripts.benchmark_media_pipeline import exclusive_run
from tests.test_media_workflow import post_ok
from tests.test_media_workflow import studio as media_studio  # noqa: F401
from tests.test_mediacms_asr_live import normalized
from tests.test_wise_live import wait_remote

pytestmark = pytest.mark.skipif(
    os.environ.get("INSPIRATION_MEDIA_AUTO_E2E") != "1",
    reason="Real automatic media acceptance requires INSPIRATION_MEDIA_AUTO_E2E=1",
)
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def automatic_window():
    required = (
        "TEST_DATABASE_URL",
        "INSPIRATION_WISE_URL",
        "INSPIRATION_WISE_TOKEN",
        "INSPIRATION_MEDIACMS_E2E_URL",
        "INSPIRATION_MEDIACMS_E2E_TOKEN",
        "E2E_SPEECH_VIDEO",
        "E2E_SPEECH_EXPECTED",
    )
    missing = [key for key in required if not os.environ.get(key)]
    assert not missing, "Set the required live-test variables: " + ", ".join(missing)
    # A completed run leaves the lock file behind: acquire it, never test exists().
    with exclusive_run(REPO / ".runtime/media-benchmark"):
        yield


def picture(color):
    output = io.BytesIO()
    Image.new("RGB", (360, 240), color).save(output, format="PNG")
    return output.getvalue()


def upload_queued(client, base, content, filename):
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
    # The same queue entry point as the upload daemon, not process(version_id).
    assert worker.run_once()
    actual = client.get(f"{base}/versions/{version['id']}").json()
    assert actual["status"] == "ready", actual
    return actual


def snapshot(sessions):
    with sessions() as db:
        return [
            {
                "id": str(row.id),
                "version_id": str(row.version_id),
                "provider": row.provider,
                "status": row.status,
                "capabilities": row.capabilities,
                "attempts": row.attempts,
                "remote_id": row.remote_id,
                "outputs": row.outputs,
                "updated_at": row.updated_at.isoformat(),
            }
            for row in db.scalars(select(MediaProcessingRun).order_by(MediaProcessingRun.id))
        ]


def cleanup_owned(sessions, remote, project, historical_ids, timeout):
    result = []
    for row in snapshot(sessions):
        if row["id"] in historical_ids:
            continue
        try:
            if row["provider"] == "wise":
                job = remote.get_job(row["id"])
                if job:
                    if job["status"] in ("queued", "processing"):
                        remote.cancel(row["id"])
                        wait_remote(remote, row["id"], timeout)
                    cleanup = remote.delete(
                        project, row["version_id"], uuid.uuid4(), source_job_id=row["id"]
                    )
                    assert wait_remote(remote, cleanup["job_id"], timeout)["status"] == "ready"
                result.append({"id": row["id"], "removed": True})
            else:
                token = token_for_upload(row["id"])
                deadline = time.monotonic() + timeout
                while True:
                    with processing_mediacms.client() as cms:
                        removed = processing_mediacms.poll_cleanup(cms, token)
                    if removed:
                        with processing_mediacms.client() as cms:
                            assert cms.get_media(token, missing=True) is None
                        break
                    assert time.monotonic() < deadline, "Owned MediaCMS cleanup timed out"
                    time.sleep(2)
                result.append({"id": row["id"], "removed": True})
        except Exception as exc:
            # Provider diagnostics may contain paths; retain only exception type.
            result.append({"id": row["id"], "removed": False, "error": type(exc).__name__})
    return result


def test_real_uploads_are_automatically_routed_without_manual_process(
    media_studio, monkeypatch, tmp_path, record_property
):
    client, sessions, ids = media_studio
    base = f"/api/v1/projects/{ids['project']}/library"
    fixture = Path(os.environ["E2E_SPEECH_VIDEO"]).resolve(strict=True)
    assert fixture.is_relative_to((REPO / ".runtime/media-integration").resolve())
    assert fixture.is_file() and fixture.stat().st_size <= 8 * 1024 * 1024
    info = probe_file(fixture, max_duration_ms=30000)
    assert info["has_video"] and info["has_audio"] and info["duration_ms"] >= 4000
    expected = os.environ["E2E_SPEECH_EXPECTED"].strip()
    assert normalized(expected), "An expected synthetic spoken phrase is required"
    wav = tmp_path / "automatic-speech.wav"
    media_engine.run(["-i", str(fixture), "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", str(wav)])
    silent = tmp_path / "automatic-silent.mp4"
    media_engine.run(
        [
            "-i",
            str(fixture),
            "-t",
            "4",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            str(silent),
        ]
    )
    for module in (worker, processing_worker):
        monkeypatch.setattr(module, "SessionLocal", sessions)
        monkeypatch.setattr(module, "engine", sessions.kw["bind"])
        monkeypatch.setattr(module, "QUEUE_LOCK", uuid.uuid4().int % (2**62))
    auto_caps = Settings.model_fields["library_processing_wise_auto_capabilities"].default
    assert auto_caps == ["visual", "audio"]
    values = {
        "library_processing_enabled": True,
        "library_processing_auto": True,
        "library_processing_wise_auto_capabilities": list(auto_caps),
        "library_wise_capabilities": ["visual", "audio", "object"],
        "library_mediacms_transcription_enabled": True,
        "library_processing_poll_seconds": 2,
        "library_processing_temp_dir": str(tmp_path / "transfer"),
        "library_staging_dir": str(tmp_path / "staging"),
        "library_wise_url": os.environ["INSPIRATION_WISE_URL"],
        "library_wise_token": os.environ["INSPIRATION_WISE_TOKEN"],
        "library_mediacms_url": os.environ["INSPIRATION_MEDIACMS_E2E_URL"],
        "library_mediacms_token": os.environ["INSPIRATION_MEDIACMS_E2E_TOKEN"],
    }
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)
    remote = WiseClient(settings.library_wise_url, settings.library_wise_token)
    health = remote.health()
    assert health["worker_alive"] is True
    assert all(
        health["capabilities"][cap]["configured"] is True for cap in ("visual", "audio", "object")
    ), "This acceptance requires the real service to expose on-demand object capability"
    timeout = int(os.environ.get("INSPIRATION_MEDIA_AUTO_TIMEOUT_SECONDS", "1200"))
    assert 120 <= timeout <= 3600
    evidence = {
        "test_run": uuid.uuid4().hex,
        "project_id": ids["project"],
        "body_passed": False,
        "cleanup_complete": False,
        "passed": False,
        "automatic": True,
        "auto_capabilities": auto_caps,
        "manual_process_requests": 0,
        "queue_driver": "real run_once, controlled test loop (not deployed daemon)",
        "historical_setup": "DB seeded failed/canceled rows; no remote failure claim",
    }
    historical = {}
    versions = {}
    try:
        sources = {
            "image": (picture("royalblue"), "automatic.png"),
            "audio": (wav.read_bytes(), "automatic.wav"),
            "video": (fixture.read_bytes(), "automatic.mp4"),
            "silent_video": (silent.read_bytes(), "automatic-silent.mp4"),
        }
        for kind, (content, filename) in sources.items():
            versions[kind] = upload_queued(client, base, content, filename)
            assert versions[kind]["media_type"] == ("video" if kind == "silent_video" else kind)
            assert versions[kind]["has_audio"] is (kind in ("audio", "video"))
        # Owned terminal histories coexist with real new work. The scanner must
        # leave them unchanged even after successful processing frees all slots.
        for state, color in (("failed", "red"), ("canceled", "green")):
            version = upload_queued(client, base, picture(color), f"history-{state}.png")
            with sessions() as db:
                row = MediaProcessingRun(
                    version_id=uuid.UUID(version["id"]),
                    created_by=ids["user"],
                    provider="wise",
                    capabilities=["visual"],
                    source_hash=version["proxy_hash"],
                    status=state,
                    attempts=1,
                    error="Synthetic preexisting terminal history",
                )
                db.add(row)
                db.commit()
                historical[str(row.id)] = state
        before_history = [row for row in snapshot(sessions) if row["id"] in historical]
        deadline = time.monotonic() + timeout
        while True:
            processing_worker.run_once()
            current = snapshot(sessions)
            actual = [row for row in current if row["id"] not in historical]
            assert all(row["status"] not in ("failed", "canceled") for row in actual), actual
            assert all("object" not in row["capabilities"] for row in actual)
            if len(actual) == 7 and all(row["status"] == "ready" for row in actual):
                break
            assert len(actual) <= 7, "Automatic scan created duplicate/unexpected provider runs"
            assert time.monotonic() < deadline, "Real automatic pipeline exceeded its deadline"
            time.sleep(1)
        expected_routes = {
            (versions["image"]["id"], "wise"): {"visual"},
            (versions["audio"]["id"], "wise"): {"audio"},
            (versions["video"]["id"], "wise"): {"visual", "audio"},
            (versions["audio"]["id"], "mediacms"): {"preview", "speech"},
            (versions["video"]["id"], "mediacms"): {"preview", "speech"},
            (versions["silent_video"]["id"], "wise"): {"visual"},
            (versions["silent_video"]["id"], "mediacms"): {"preview"},
        }
        assert {(row["version_id"], row["provider"]) for row in actual} == set(expected_routes)
        for row in actual:
            assert set(row["capabilities"]) == expected_routes[row["version_id"], row["provider"]]
            if row["provider"] == "wise":
                job = remote.get_job(row["id"])
                assert job["status"] == "ready"
                for cap in row["capabilities"]:
                    assert job["capabilities"][cap]["state"] == "ready"
                    assert job["capabilities"][cap]["vector_count"] > 0
                assert job["capabilities"]["object"]["vector_count"] == 0
            else:
                assert row["outputs"]["upstream_removed"]
                assert row["outputs"]["encoding_completion"]["complete"]
                assert row["outputs"]["proxy_hash"]
                if "speech" in row["capabilities"]:
                    assert row["outputs"]["transcript_hash"]
                else:
                    assert not row["outputs"].get("transcript_hash")
                with processing_mediacms.client() as cms:
                    assert cms.get_media(token_for_upload(row["id"]), missing=True) is None
        for kind, version in versions.items():
            with sessions() as db:
                stored = db.get(MediaVersion, uuid.UUID(version["id"]))
                original = sources[kind][0]
                assert stored.original_hash == hashlib.sha256(original).hexdigest()
                assert cas.read_bytes(db.get(Blob, stored.original_hash)) == original
                with cas.local_file(db.get(Blob, stored.proxy_hash)) as path:
                    if kind == "image":
                        with Image.open(path) as preview:
                            preview.verify()
                    else:
                        media_engine.run(["-i", str(path), "-f", "null", "-"])
                if kind != "image":
                    cms_run = next(
                        row
                        for row in actual
                        if row["provider"] == "mediacms" and row["version_id"] == version["id"]
                    )
                    assert stored.proxy_hash == cms_run["outputs"]["proxy_hash"]
                    cues = list(
                        db.scalars(
                            select(MediaAnnotation).where(
                                MediaAnnotation.version_id == stored.id,
                                MediaAnnotation.kind == "speech",
                            )
                        )
                    )
                    if kind == "silent_video":
                        assert cues == [] and stored.has_audio is False
                    else:
                        assert cues and all(
                            cue.source.get("provider") == "mediacms" for cue in cues
                        )
                        assert all(
                            0 <= cue.start_ms < cue.end_ms <= stored.duration_ms for cue in cues
                        )
                        assert normalized(expected) in normalized(
                            " ".join(cue.text for cue in cues)
                        )
        speech = post_ok(
            client,
            f"{base}/search",
            {
                "query": expected,
                "mode": "annotated",
                "kind": "speech",
                "limit": 100,
            },
        )
        assert {hit["version_id"] for hit in speech["items"]} == {
            versions["audio"]["id"],
            versions["video"]["id"],
        }
        for modality, kinds in (
            ("visual", ("image", "video", "silent_video")),
            ("audio", ("audio", "video")),
        ):
            found = post_ok(
                client,
                f"{base}/search",
                {
                    "engine": "wise",
                    "target": modality,
                    "query": "a blue picture" if modality == "visual" else "speech",
                    "limit": 100,
                },
            )
            assert found["coverage"]["indexed"] == len(kinds)
            assert {hit["version_id"] for hit in found["items"]} == {
                versions[kind]["id"] for kind in kinds
            }
            # Ranking quality is not asserted on this tiny synthetic collection.
        stable = snapshot(sessions)
        for _ in range(5):
            assert processing_worker.run_once() is False
        assert snapshot(sessions) == stable
        assert [row for row in stable if row["id"] in historical] == before_history
        assert all(remote.get_job(job_id) is None for job_id in historical)
        evidence.update(
            body_passed=True, runs=stable, versions=versions, speech_hits=speech["total"]
        )
    finally:
        monkeypatch.setattr(settings, "library_processing_auto", False)
        evidence["cleanup"] = cleanup_owned(sessions, remote, ids["project"], historical, timeout)
        removed = all(item["removed"] for item in evidence["cleanup"])
        if removed:
            try:
                for modality in ("visual", "audio"):
                    result = remote.search(
                        ids["project"],
                        {
                            "query": "speech",
                            "modality": modality,
                            "source_ids": [version["id"] for version in versions.values()],
                            "limit": 100,
                        },
                    )
                    assert result["items"] == []
                evidence["cleanup_search_empty"] = True
            except Exception as exc:
                removed = False
                evidence["cleanup_search_error"] = type(exc).__name__
        evidence["cleanup_complete"] = removed
        evidence["passed"] = evidence["body_passed"] and evidence["cleanup_complete"]
        output = REPO / ".runtime/media-integration/automatic" / f"{evidence['test_run']}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        record_property("automatic_evidence", str(output))
        assert removed, "Owned provider cleanup incomplete"
