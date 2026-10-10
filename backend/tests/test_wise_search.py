"""Real authorization/coverage SQL with explicit fake remote search responses."""

# ruff: noqa: F811
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from app.models.library import MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.modules.library import wise_search
from tests.test_library import library, media_studio  # noqa: F401
from tests.test_media_processing import setup
from tests.test_media_workflow import post_ok


def test_scope_and_frame_hits_and_unavailable_audio(library, monkeypatch):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    before = post_ok(client, f"{base}/search", {"engine": "wise", "query": "rain"})
    assert before["items"] == []
    assert before["coverage"]["versions"] == before["coverage"]["unindexed"] == 1
    assert before["coverage"]["indexed"] == 0
    task = post_ok(
        client,
        f"{base}/versions/{version['id']}/process",
        {
            "provider": "wise",
            "capabilities": ["visual", "audio"],
        },
    )
    with sessions() as db:
        run = db.get(MediaProcessingRun, uuid.UUID(task["id"]))
        run.status = "ready"
        run.outputs = {
            "capabilities": {
                "visual": {"state": "ready", "vector_count": 4},
                "audio": {"state": "unavailable", "vector_count": 0},
            }
        }
        db.commit()

    class Remote:
        def __init__(self, *args):
            pass

        def search(self, project, payload):
            assert str(project) == ids["project"]
            assert payload["source_ids"] == [version["id"]]
            return {
                "items": [
                    # Unknown IDs may never become browser results even from a bad upstream.
                    {
                        "source_id": str(uuid.uuid4()),
                        "start_seconds": 0,
                        "end_seconds": 1,
                        "score": 0.9,
                    },
                    {
                        "source_id": version["id"],
                        "start_seconds": 1,
                        "end_seconds": 1,
                        "score": 0.8,
                    },
                    {
                        "source_id": version["id"],
                        "start_seconds": 20,
                        "end_seconds": 21,
                        "score": 0.7,
                    },
                    {
                        "source_id": version["id"],
                        "start_seconds": -5,
                        "end_seconds": "NaN",
                        "score": 0.5,
                    },
                    None,
                ]
            }

    monkeypatch.setattr(wise_search, "WiseClient", Remote)
    data = {"engine": "wise", "target": "visual", "query": "红色的画面"}
    response = post_ok(client, f"{base}/search", data)
    assert len(response["items"]) == 1
    hit = response["items"][0]
    assert hit["match_type"] == "frame" and hit["matched_at_ms"] == 1000
    assert hit["start_ms"] == 1000 and hit["end_ms"] == 1500
    assert response["coverage"]["indexed"] == 1
    assert not response["has_more"]
    audio = post_ok(client, f"{base}/search", {**data, "target": "audio"})
    assert audio["items"] == [] and audio["coverage"]["unindexed"] == 1
    assert post_ok(client, f"{base}/search", {**data, "offset": 96})["items"] == []
    assert post_ok(client, f"{base}/{version['media_id']}/trash")["physical_delete"] is False
    assert post_ok(client, f"{base}/search", data)["items"] == []


def test_combined_query_is_explicitly_rejected(library, monkeypatch):
    client, _, _, base, _ = setup(library, monkeypatch)
    response = client.post(
        f"{base}/search",
        json={
            "engine": "wise",
            "query": "sea",
            "image": "data:image/png;base64,eA==",
        },
    )
    assert response.status_code == 422
    assert "单独" in response.json()["error"]["message"]


def test_audio_scope_and_coverage_exclude_confirmed_silent_video_only(library, monkeypatch):
    client, sessions, ids, base, uploaded = setup(library, monkeypatch)
    with sessions() as db:
        silent = db.get(MediaVersion, uuid.UUID(uploaded["id"]))
        silent.has_audio = False
        versions = [silent]
        for ordinal, has_audio in enumerate((True, None, None), start=2):
            version = MediaVersion(
                media_id=silent.media_id,
                ordinal=ordinal,
                filename=f"video-{ordinal}.mp4",
                media_type="video",
                has_audio=has_audio,
                fingerprint=silent.fingerprint,
                size_bytes=silent.size_bytes,
                status="ready",
                proxy_hash=silent.proxy_hash,
                duration_ms=2000,
            )
            db.add(version)
            versions.append(version)
        db.flush()
        # Explicit historical provider results, including an obsolete ready
        # audio result for the now-confirmed silent video. No remote inference.
        for version in versions[:3]:
            db.add(
                MediaProcessingRun(
                    version_id=version.id,
                    created_by=ids["user"],
                    provider="wise",
                    capabilities=["visual", "audio"],
                    source_hash=version.proxy_hash,
                    status="ready",
                    outputs={
                        "capabilities": {
                            cap: {"state": "ready", "vector_count": 1}
                            for cap in ("visual", "audio")
                        }
                    },
                )
            )
        indexed_ids = [str(version.id) for version in versions[:3]]
        db.commit()

    class Remote:
        def __init__(self, *_):
            pass

        def search(self, _project, payload):
            expected = indexed_ids[1:] if payload["modality"] == "audio" else indexed_ids
            assert set(payload["source_ids"]) == set(expected)
            return {
                "items": [
                    {"source_id": source, "start_seconds": 0, "end_seconds": 1, "score": 0.8}
                    for source in indexed_ids
                ]
            }

    monkeypatch.setattr(wise_search, "WiseClient", Remote)
    for target, expected_ids, total in (
        ("audio", indexed_ids[1:], 3),
        ("visual", indexed_ids, 4),
    ):
        result = post_ok(
            client, f"{base}/search", {"engine": "wise", "target": target, "query": "rain"}
        )
        assert {item["version_id"] for item in result["items"]} == set(expected_ids)
        assert result["coverage"]["versions"] == total
        assert result["coverage"]["indexed"] == len(expected_ids)
        assert result["coverage"]["unindexed"] == 1


def test_adding_audio_preserves_visual_coverage_but_new_visual_result_supersedes_old(
    library, monkeypatch
):
    client, sessions, _, base, version = setup(library, monkeypatch)

    def complete(cap, state):
        task = post_ok(
            client,
            f"{base}/versions/{version['id']}/process",
            {"provider": "wise", "capabilities": [cap]},
        )
        with sessions() as db:
            row = db.get(MediaProcessingRun, uuid.UUID(task["id"]))
            row.status = "ready"
            row.outputs = {
                "capabilities": {
                    cap: {"state": state, "vector_count": 4 if state == "ready" else 0}
                }
            }
            db.commit()

    class Remote:
        def __init__(self, *_):
            pass

        def search(self, _project, payload):
            assert payload["source_ids"] == [version["id"]]
            return {
                "items": [
                    {"source_id": version["id"], "start_seconds": 0, "end_seconds": 1, "score": 0.8}
                ]
            }

    monkeypatch.setattr(wise_search, "WiseClient", Remote)
    complete("visual", "ready")
    complete("audio", "ready")
    for target in ("visual", "audio"):
        result = post_ok(
            client, f"{base}/search", {"engine": "wise", "target": target, "query": "rain"}
        )
        assert result["coverage"]["indexed"] == 1 and result["items"]
    complete("visual", "unavailable")
    result = post_ok(client, f"{base}/search", {"engine": "wise", "query": "rain"})
    assert result["coverage"]["indexed"] == 0 and not result["items"]


@pytest.mark.parametrize("change", ["trash", "restore", "restore_reindexed"])
def test_inflight_search_rechecks_local_deletion_and_processing_generation(
    library, monkeypatch, change
):
    client, sessions, _, base, version = setup(library, monkeypatch)
    task = post_ok(
        client,
        f"{base}/versions/{version['id']}/process",
        {"provider": "wise", "capabilities": ["visual"]},
    )

    def complete(task_id):
        with sessions() as db:
            run = db.get(MediaProcessingRun, uuid.UUID(task_id))
            run.attempts = 1
            run.status = "ready"
            run.outputs = {
                **run.outputs,
                "capabilities": {"visual": {"state": "ready", "vector_count": 4}},
            }
            db.commit()

    complete(task["id"])
    entered, release = threading.Event(), threading.Event()

    class Remote:
        """Explicit remote double: cleanup has not reached its old snapshot."""

        def __init__(self, *_):
            pass

        def search(self, _project, payload):
            assert payload["source_ids"] == [version["id"]]
            entered.set()
            assert release.wait(10), "Test failed to release the remote query"
            return {
                "items": [
                    {
                        "source_id": version["id"],
                        "start_seconds": 0,
                        "end_seconds": 1,
                        "score": 0.8,
                    }
                ]
            }

    monkeypatch.setattr(wise_search, "WiseClient", Remote)
    request = {"engine": "wise", "target": "visual", "query": "rain"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, f"{base}/search", json=request)
        try:
            assert entered.wait(10)
            post_ok(client, f"{base}/{version['media_id']}/trash")
            with sessions() as db:
                cleanup = db.scalar(
                    select(MediaProcessingRun).where(
                        MediaProcessingRun.version_id == uuid.UUID(version["id"]),
                        MediaProcessingRun.operation == "delete",
                    )
                )
                assert cleanup.status == "queued" and cleanup.attempts == 0
                # No actual provider calls; the real local outbox remains queued
                # throughout the two primary trash/restore race scenarios.
                if change == "restore_reindexed":
                    cleanup.status = "ready"
                    db.commit()
            if change != "trash":
                post_ok(client, f"{base}/{version['media_id']}/restore")
            if change == "restore_reindexed":
                replacement = post_ok(
                    client,
                    f"{base}/versions/{version['id']}/process",
                    {"provider": "wise", "capabilities": ["visual"]},
                )
                complete(replacement["id"])
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["items"] == [] and result["has_more"] is False
    assert result["coverage"]["indexed"] == (1 if change == "restore_reindexed" else 0)
    assert result["coverage"]["versions"] == (0 if change == "trash" else 1)
    assert result["coverage"]["unindexed"] == (1 if change == "restore" else 0)
    if change == "restore_reindexed":
        # Only a new request may use the newly accepted run; old in-flight
        # vectors may not be relabeled as results from the restored generation.
        fresh = post_ok(client, f"{base}/search", request)
        assert fresh["items"][0]["version_id"] == version["id"]
