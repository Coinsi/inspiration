"""Opt-in business acceptance of genuine visual + OWLv2 processing together.

Requires INSPIRATION_WISE_OBJECT_E2E=1, INSPIRATION_WISE_OBJECT_IMAGE (an owned
local cup PNG), INSPIRATION_WISE_URL/TOKEN and TEST_DATABASE_URL. Read-only
snapshot access uses INSPIRATION_WISE_CONTAINER or INSPIRATION_WISE_DATA_DIR,
as documented in test_wise_incremental_live. The bridge must already have passed
its real offline object probe and explicitly enabled visual/object. No weights
are downloaded, no readiness guard is bypassed and no HTTP/model is mocked.

The fixture is the image plus a real four-second, two-fps silent video of it.
Both capabilities are requested in ONE task per source, exercising their actual
combined extraction memory use. This bounded pipeline check is not an object
recognition quality score, nor a guarantee that hour-long jobs fit in memory.
"""

# ruff: noqa: F811
import base64
import hashlib
import io
import json
import math
import os
import time
import uuid
import warnings
from collections import defaultdict
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import settings
from app.modules.generation import media_engine
from app.modules.library import optional_capabilities, processing_worker, worker
from app.modules.library.providers.wise import WiseClient
from app.modules.library.schemas import SearchBBox
from tests.test_media_workflow import post_ok
from tests.test_media_workflow import studio as media_studio  # noqa: F401
from tests.test_wise_incremental_live import read_snapshot  # noqa: F401
from tests.test_wise_live import live_settings, wait_local, wait_remote  # noqa: F401
from tests.test_wise_multimodal_live import upload_media

pytestmark = pytest.mark.skipif(
    os.environ.get("INSPIRATION_WISE_OBJECT_E2E") != "1",
    reason="Real object acceptance requires INSPIRATION_WISE_OBJECT_E2E=1",
)


@pytest.fixture
def cup_media(tmp_path):
    value = os.environ.get("INSPIRATION_WISE_OBJECT_IMAGE")
    assert value, "Set INSPIRATION_WISE_OBJECT_IMAGE to the owned local cup PNG"
    source = Path(value)
    assert source.is_file() and 0 < source.stat().st_size <= 2 * 1024**2
    content = source.read_bytes()
    with Image.open(io.BytesIO(content)) as image:
        assert image.format == "PNG" and image.width * image.height <= 20_000_000
        image.verify()
    video = tmp_path / "cup-four-seconds.mp4"
    media_engine.run(
        [
            "-loop",
            "1",
            "-i",
            str(source),
            "-t",
            "4",
            "-r",
            "2",
            "-an",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            str(video),
        ]
    )
    return content, video.read_bytes()


def validate_business_regions(items, versions):
    assert items, "Real object query must produce usable regions"
    valid_versions = {item["id"]: item for item in versions}
    identities = set()
    by_frame = defaultdict(set)
    for hit in items:
        assert hit["version_id"] in valid_versions
        version = valid_versions[hit["version_id"]]
        SearchBBox.model_validate(hit["bbox"])
        assert hit["vector_id"].isascii() and hit["vector_id"].isdecimal()
        assert math.isfinite(hit["score"])
        identity = (hit["version_id"], hit["vector_id"])
        assert identity not in identities, "Object vector identity was duplicated"
        identities.add(identity)
        by_frame[(hit["version_id"], hit["matched_at_ms"])].add(hit["id"])
        assert hit["media_type"] == version["media_type"]
        if version["media_type"] == "image":
            assert hit["match_type"] == "image"
            assert hit["start_ms"] is None and hit["end_ms"] is None
            assert hit["matched_at_ms"] is None
        else:
            assert hit["match_type"] == "frame"
            assert 0 <= hit["start_ms"] < hit["end_ms"] <= version["duration_ms"]
            assert hit["matched_at_ms"] == hit["start_ms"]
    return identities, by_frame


def test_real_visual_and_object_tasks_query_regions_and_delete(
    media_studio,
    cup_media,
    monkeypatch,
    read_snapshot,
    record_property,
):
    client, sessions, ids = media_studio
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "object"])
    optional_capabilities._cache.clear()
    remote = WiseClient(settings.library_wise_url, settings.library_wise_token)
    health = remote.health()
    assert health.get("worker_alive") is True
    state = health.get("capabilities", {}).get("object", {})
    assert state.get("configured") is True and state.get("ready") is True
    assert state.get("integrity_verified") is True
    assert state.get("validation", {}).get("protocol") == "wise-optional-probe-v1"
    assert optional_capabilities.optional_states(refresh=True)["object"]["ready"] is True
    timeout = int(os.environ.get("INSPIRATION_WISE_TIMEOUT_SECONDS", "600"))
    assert 30 <= timeout <= 3600
    project_id = ids["project"]
    base = f"/api/v1/projects/{project_id}/library"
    tracked, versions, observations = [], [], []
    succeeded = False
    previous = None
    try:
        for ordinal, (content, filename) in enumerate(
            zip(cup_media, ("cup.png", "cup.mp4"), strict=True)
        ):
            version = upload_media(client, base, content, filename)
            task = post_ok(
                client,
                f"{base}/versions/{version['id']}/process",
                {"provider": "wise", "capabilities": ["visual", "object"]},
            )
            tracked.append((version["id"], task["id"]))
            started = time.monotonic()
            result = wait_local(client, base, task["id"], timeout)
            for capability in ("visual", "object"):
                assert result["capability_status"][capability]["state"] == "ready"
                assert result["capability_status"][capability]["vector_count"] > 0
            upstream = remote.get_job(task["id"])
            assert upstream and upstream["status"] == "ready"
            snapshot = read_snapshot(project_id, upstream["revision"])
            versions.append(version)
            assert snapshot["source_ids"] == sorted(item["id"] for item in versions)
            assert snapshot["counts"][version["id"]]["visual"] == (1 if ordinal == 0 else 8)
            assert snapshot["counts"][version["id"]]["object"] > 0
            assert snapshot["build"] == {"reused_sources": ordinal, "extracted_sources": 1}
            if previous:
                assert read_snapshot(project_id, previous["revision"]) == previous
            previous = snapshot
            observations.append(
                {
                    "stage": "combined_upsert",
                    "seconds": time.monotonic() - started,
                    "capabilities": result["capability_status"],
                    **snapshot,
                }
            )

        image, video = versions
        request = {
            "engine": "wise",
            "target": "object",
            "query": "a cup",
            "limit": 100,
            "collapse_versions": False,
        }
        initial = post_ok(client, f"{base}/search", request)
        assert initial["coverage"]["indexed"] == 2
        identities, frames = validate_business_regions(initial["items"], versions)
        assert {hit["version_id"] for hit in initial["items"]} == {image["id"], video["id"]}
        assert any(len(regions) > 1 for regions in frames.values()), (
            "This fixture did not exercise multiple real regions at one frame; "
            "do not claim region-preservation acceptance from a single detection"
        )
        # Compare to the real upstream top-100 with exactly the same scope. No
        # synthetic duplicate boxes are injected to make this regression pass.
        upstream_hits = remote.search(
            project_id,
            {
                "modality": "object",
                "query": "a cup",
                "limit": 100,
                "source_ids": [image["id"], video["id"]],
            },
        )["items"]
        for hit in upstream_hits:
            SearchBBox.model_validate(hit["bbox"])
        assert identities == {(hit["source_id"], hit["vector_id"]) for hit in upstream_hits}
        repeated = post_ok(client, f"{base}/search", request)
        assert [hit["id"] for hit in repeated["items"]] == [hit["id"] for hit in initial["items"]]
        grouped = post_ok(client, f"{base}/search", {**request, "collapse_versions": True})
        assert {match["id"] for hit in grouped["items"] for match in hit["matches"]} == {
            hit["id"] for hit in initial["items"]
        }
        image_hit = next(hit for hit in initial["items"] if hit["version_id"] == image["id"])
        region_request = {
            "engine": "wise",
            "target": "object",
            "limit": 100,
            "image": "data:image/png;base64," + base64.b64encode(cup_media[0]).decode(),
            "bbox": image_hit["bbox"],
        }
        regional = post_ok(client, f"{base}/search", region_request)
        validate_business_regions(regional["items"], versions)
        assert regional["coverage"]["indexed"] == 2
        visual_request = {"engine": "wise", "target": "visual", "query": "a cup", "limit": 100}
        visual = post_ok(client, f"{base}/search", visual_request)
        assert visual["coverage"]["indexed"] == 2
        assert {hit["version_id"] for hit in visual["items"]} == {image["id"], video["id"]}
        # Moving from object to visual and back also exercises bounded model
        # cache eviction, rather than only querying the encoder loaded first.
        again = post_ok(client, f"{base}/search", request)
        assert validate_business_regions(again["items"], versions)[0] == identities
        assert (
            client.post(f"/api/v1/projects/{ids['other']}/library/search", json=request).status_code
            == 403
        )
        assert (
            remote.search(
                project_id,
                {
                    "modality": "object",
                    "query": "a cup",
                    "limit": 100,
                    "source_ids": [str(uuid.uuid4())],
                },
            )["items"]
            == []
        )
        observations.append(
            {
                "stage": "queries",
                "text_hits": len(initial["items"]),
                "region_hits": len(regional["items"]),
                "distinct_frame_groups": len(frames),
                "max_regions_per_frame": max(map(len, frames.values())),
                "image_sha256": hashlib.sha256(cup_media[0]).hexdigest(),
                "hits": initial["items"],
            }
        )

        post_ok(client, f"{base}/{image['media_id']}/trash")
        rows = client.get(f"{base}/processing/runs?limit=100")
        assert rows.status_code == 200
        cleanup = next(row for row in rows.json()["items"] if row["operation"] == "delete")
        started = time.monotonic()
        wait_local(client, base, cleanup["id"], timeout)
        deleted = remote.get_job(cleanup["id"])
        assert deleted and deleted["status"] == "ready"
        snapshot = read_snapshot(project_id, deleted["revision"])
        assert snapshot["build"] == {"reused_sources": 1, "extracted_sources": 0}
        assert snapshot["has_extraction_listing"] is False
        assert snapshot["source_ids"] == [video["id"]]
        observations.append({"stage": "delete", "seconds": time.monotonic() - started, **snapshot})
        for search_request in (request, region_request, visual_request):
            result = post_ok(client, f"{base}/search", search_request)
            assert result["coverage"]["indexed"] == 1 and result["items"]
            assert {hit["version_id"] for hit in result["items"]} == {video["id"]}
            if search_request["target"] == "object":
                validate_business_regions(result["items"], [video])
        assert (
            remote.search(
                project_id,
                {
                    "modality": "object",
                    "query": "a cup",
                    "limit": 100,
                    "source_ids": [image["id"]],
                },
            )["items"]
            == []
        )
        succeeded = True
    finally:
        record_property("wise_object_observations", json.dumps(observations, ensure_ascii=False))
        errors = []
        for source_id, anchor in tracked:
            try:
                known_anchor = anchor if remote.get_job(anchor) else None
                cleanup = remote.delete(
                    project_id, source_id, uuid.uuid4(), source_job_id=known_anchor
                )
                assert wait_remote(remote, cleanup["job_id"], timeout)["status"] == "ready"
            except Exception as exc:
                errors.append(type(exc).__name__)
        optional_capabilities._cache.clear()
        if errors:
            message = "Object live WISE cleanup failed: " + ", ".join(errors)
            if succeeded:
                pytest.fail(message)
            warnings.warn(message, RuntimeWarning, stacklevel=1)
