"""Checkpoint resume, cancellation, review publication and source separation."""

# ruff: noqa: F811
import base64
import io
import uuid
import wave

from sqlalchemy import select

from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaAnnotation
from app.models.transcription import TranscriptionRun
from app.modules.generation import media_engine
from app.modules.transcription import service, worker
from app.storage import cas
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def seed(tmp_path, sessions, ids):
    path = tmp_path / "speech.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=160x90:r=5:d=32",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=32",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    with sessions() as db:
        blob = cas.put_file(db, path, "video/mp4", duration_ms=32000)
        m = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="Checkpoint test")
        db.add(m)
        db.flush()
        v = MediaVersion(
            media_id=m.id,
            ordinal=1,
            filename="speech.mp4",
            fingerprint="a" * 64,
            size_bytes=path.stat().st_size,
            uploaded_bytes=path.stat().st_size,
            status="ready",
            duration_ms=32000,
            proxy_hash=blob.hash,
            original_hash=blob.hash,
        )
        db.add(v)
        db.commit()
        return str(m.id), str(v.id)


def test_resume_review_publication_and_edit_conflict(studio, tmp_path, monkeypatch):
    c, sessions, ids = studio
    m, v = seed(tmp_path, sessions, ids)
    root = f"/api/v1/projects/{ids['project']}"
    base = root + "/transcriptions"
    calls = []

    def inference(path, payload=None):
        if path == "/health":
            return {"model_key": "test-asr-v1"}
        with wave.open(io.BytesIO(base64.b64decode(payload["audio"]))) as wav:
            duration = round(wav.getnframes() / 16000 * 1000)
        calls.append(duration)
        if len(calls) == 2:
            raise ValueError("Test outage")
        return {
            "model_key": "test-asr-v1",
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": duration,
                    "text": "测试语音" if duration > 2000 else "续接片段",
                }
            ],
        }

    monkeypatch.setattr(service, "inference", inference)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    r = post_ok(c, base, {"version_id": v, "language": "zh"})
    assert post_ok(c, base, {"version_id": v})["id"] == r["id"]
    assert not c.get(root + f"/library/{m}/deletion-impact").json()["can_trash"]
    worker.process(uuid.UUID(r["id"]))
    failed = c.get(base + "/" + r["id"]).json()
    assert failed["status"] == "failed" and failed["completed"] == 1
    post_ok(c, base + "/" + r["id"] + "/control", {"action": "retry"})
    worker.process(uuid.UUID(r["id"]))
    ready = c.get(base + "/" + r["id"]).json()
    assert ready["status"] == "ready"
    assert calls == [30000, 2000, 2000]
    assert ready["cues"][1]["start_ms"] == 30000
    with sessions() as db:
        assert not list(db.scalars(select(MediaAnnotation)))
    post_ok(
        c,
        root + f"/library/versions/{v}/annotations",
        {"start_ms": 0, "end_ms": 1000, "kind": "visual", "text": "手工画面证据"},
    )
    cues = ready["cues"]
    cues[0]["text"] = "核对后的台词"
    saved = c.put(base + "/" + r["id"], json={"revision": 0, "cues": cues})
    assert saved.status_code == 200
    assert c.put(base + "/" + r["id"], json={"revision": 0, "cues": cues}).status_code == 409
    assert c.post(base + "/" + r["id"] + "/publish", json={"revision": 0}).status_code == 409
    post_ok(c, base + "/" + r["id"] + "/publish", {"revision": 1})
    post_ok(c, base + "/" + r["id"] + "/publish", {"revision": 1})
    with sessions() as db:
        annotations = list(db.scalars(select(MediaAnnotation)))
        assert len(annotations) == 3
        assert all(a.source["reviewed"] for a in annotations if a.kind == "speech")
        assert db.get(TranscriptionRun, uuid.UUID(r["id"])).raw_cues[0]["text"] == "测试语音"
    srt = c.get(base + "/" + r["id"] + "/srt")
    assert "核对后的台词" in srt.text and "00:00:30,000" in srt.text
    r2 = post_ok(c, base, {"version_id": v, "new_run": True})
    worker.process(uuid.UUID(r2["id"]))
    post_ok(c, base + "/" + r2["id"] + "/publish", {"revision": 0})
    with sessions() as db:
        assert len(list(db.scalars(select(MediaAnnotation)))) == 3
        assert db.get(TranscriptionRun, uuid.UUID(r["id"])).published_revision is None


def test_canceled_inflight_result_is_not_published(studio, tmp_path, monkeypatch):
    c, sessions, ids = studio
    m, v = seed(tmp_path, sessions, ids)
    base = f"/api/v1/projects/{ids['project']}/transcriptions"
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(service, "inference", lambda *_: {"model_key": "test"})
    run = post_ok(c, base, {"version_id": v})

    def canceled(path, payload=None):
        with sessions() as db:
            db.get(TranscriptionRun, uuid.UUID(run["id"])).cancel_requested = True
            db.commit()
        return {
            "model_key": "test",
            "segments": [{"start_ms": 0, "end_ms": 1000, "text": "不得落地"}],
        }

    monkeypatch.setattr(service, "inference", canceled)
    worker.process(uuid.UUID(run["id"]))
    r = c.get(base + "/" + run["id"]).json()
    assert r["status"] == "canceled" and r["completed"] == 0 and not r["cues"]
    assert c.post(base + "/" + run["id"] + "/publish", json={"revision": 0}).status_code == 409
