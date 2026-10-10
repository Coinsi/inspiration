"""Type-aware external processing and real bounded reference decoding."""

# ruff: noqa: F811
import base64
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import CapabilityUnsupported
from app.models.library import MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.modules.generation import media_engine
from app.modules.library import processing, wise_search
from tests.test_library import library, media_studio  # noqa: F401
from tests.test_media_processing import setup
from tests.test_media_workflow import post_ok


@pytest.mark.parametrize("kind", ["image", "audio"])
@pytest.mark.parametrize("speech", [False, True])
def test_automatic_processing_queues_only_applicable_media_work(library, monkeypatch, kind, speech):
    _, sessions, _, _, version = setup(library, monkeypatch)
    monkeypatch.setattr(settings, "library_processing_auto", True)
    monkeypatch.setattr(settings, "library_mediacms_url", "http://cms.test")
    monkeypatch.setattr(settings, "library_mediacms_token", "test-token")
    monkeypatch.setattr(settings, "library_mediacms_transcription_enabled", speech)
    with sessions() as db:
        db.get(MediaVersion, uuid.UUID(version["id"])).media_type = kind
        db.commit()
        assert processing.enqueue_automatic(db)
        assert processing.enqueue_automatic(db) is (kind == "audio")
        assert processing.enqueue_automatic(db) is False
        jobs = {row.provider: row.capabilities for row in db.scalars(select(MediaProcessingRun))}
    assert jobs == (
        {"wise": ["visual"]}
        if kind == "image"
        else {"wise": ["audio"], "mediacms": ["preview", "speech"] if speech else ["preview"]}
    )


@pytest.mark.parametrize(
    "kind,good,bad", [("image", "visual", "audio"), ("audio", "audio", "visual")]
)
def test_kind_limits_capabilities_and_coverage(library, monkeypatch, kind, good, bad):
    client, sessions, _, base, version = setup(library, monkeypatch)
    with sessions() as db:
        row = db.get(MediaVersion, uuid.UUID(version["id"]))
        row.media_type = kind
        if kind == "image":
            row.duration_ms = None
        db.commit()
    rejected = client.post(
        f"{base}/versions/{version['id']}/process", json={"provider": "wise", "capabilities": [bad]}
    )
    assert rejected.status_code == 422
    task = post_ok(
        client,
        f"{base}/versions/{version['id']}/process",
        {"provider": "wise", "capabilities": [good]},
    )
    with sessions() as db:
        run = db.get(MediaProcessingRun, uuid.UUID(task["id"]))
        run.status = "ready"
        run.outputs = {"capabilities": {good: {"state": "ready", "vector_count": 1}}}
        db.commit()

    class Remote:
        def __init__(self, *_):
            pass

        def search(self, _, data):
            return {
                "items": [
                    {
                        "source_id": version["id"],
                        "score": 0.8,
                        "start_seconds": None if kind == "image" else 0,
                        "end_seconds": None if kind == "image" else 1,
                    }
                ]
            }

    monkeypatch.setattr(wise_search, "WiseClient", Remote)
    result = post_ok(client, f"{base}/search", {"engine": "wise", "target": good, "query": "rain"})
    hit = result["items"][0]
    assert hit["media_type"] == kind
    if kind == "image":
        assert hit["start_ms"] is None and hit["end_ms"] is None and hit["match_type"] == "image"
    else:
        assert hit["start_ms"] == 0 and hit["end_ms"] == 1000 and hit["match_type"] == "interval"
    invalid = post_ok(client, f"{base}/search", {"engine": "wise", "target": bad, "query": "rain"})
    assert invalid["coverage"]["versions"] == 0 and invalid["items"] == []
    if kind == "image":
        monkeypatch.setattr(settings, "library_mediacms_url", "http://cms.test")
        monkeypatch.setattr(settings, "library_mediacms_token", "test-token")
        assert (
            client.post(
                f"{base}/versions/{version['id']}/process",
                json={"provider": "mediacms", "capabilities": ["preview"]},
            ).status_code
            == 422
        )


def test_reference_audio_decodes_real_audio_and_rejects_overlong_or_video(tmp_path):
    for duration, valid in [(4.2, True), (31, False)]:
        path = tmp_path / f"tone-{duration}.wav"
        media_engine.run(
            ["-f", "lavfi", "-i", f"sine=frequency=880:duration={duration}", str(path)]
        )
        value = base64.b64encode(path.read_bytes()).decode()
        if valid:
            result = wise_search.reference_audio(value)
            assert result.startswith(b"RIFF") and len(result) < 4 * 1024**2
        else:
            with pytest.raises(CapabilityUnsupported):
                wise_search.reference_audio(value)
    with pytest.raises(CapabilityUnsupported):
        wise_search.reference_audio(base64.b64encode(b"invalid").decode())
