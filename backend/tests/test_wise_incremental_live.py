"""Opt-in real incremental WISE acceptance, not a retrieval-quality benchmark.

Set INSPIRATION_WISE_INCREMENTAL_E2E=1, INSPIRATION_WISE_URL,
INSPIRATION_WISE_TOKEN and TEST_DATABASE_URL. Also provide exactly one read-only
snapshot access method: INSPIRATION_WISE_DATA_DIR (the bridge's data directory),
or INSPIRATION_WISE_CONTAINER (an already-running Docker container). The container
reader uses its WISE_BRIDGE_DATA and Python at INSPIRATION_WISE_CONTAINER_PYTHON
(default /opt/conda/envs/wise-env/bin/python). It never builds/starts a container,
downloads weights, modifies snapshots, or substitutes mocked embeddings.

One disposable project receives a 2s video, a PNG, then another 2s video. Real
manifest counters and extraction input lists must show only new-source encoding;
the previous published snapshot must remain byte-for-byte unchanged on upsert.
Deleting the PNG must reuse both remaining sources without any extraction, and
actual text/image queries must still resolve only the remaining versions.
Successful deletion intentionally removes obsolete snapshots with deleted data.
"""

# ruff: noqa: F811
import base64
import inspect
import io
import json
import math
import os
import subprocess
import time
import uuid
import warnings

import pytest
from PIL import Image

from app.core.config import settings
from app.modules.library import processing_worker, worker
from app.modules.library.providers.wise import WiseClient
from tests.test_media_workflow import changing_video, post_ok  # noqa: F401
from tests.test_media_workflow import studio as media_studio  # noqa: F401
from tests.test_wise_live import live_settings, wait_local, wait_remote  # noqa: F401
from tests.test_wise_multimodal_live import upload_media

pytestmark = pytest.mark.skipif(
    os.environ.get("INSPIRATION_WISE_INCREMENTAL_E2E") != "1",
    reason="Real incremental acceptance requires INSPIRATION_WISE_INCREMENTAL_E2E=1",
)


def snapshot_observation(data_dir, project_id, revision):
    """Pure reader; copied unchanged into the selected container via Python -c."""
    import hashlib
    import json
    import uuid
    from pathlib import Path

    root = Path(data_dir).resolve()
    project_id, revision = str(uuid.UUID(project_id)), str(uuid.UUID(revision))
    snapshot = root / "projects" / project_id / "snapshots" / revision
    assert snapshot.resolve() == snapshot, "Snapshot must not be a link"
    manifest_path = snapshot / "manifest.json"
    assert manifest_path.is_file(), "Published snapshot is missing in selected data store"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256()
    files = [manifest_path, *sorted((snapshot / "engine").rglob("*"))]
    for path in files:
        assert not path.is_symlink(), "Unexpected snapshot symlink"
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        fingerprint.update(path.relative_to(snapshot).as_posix().encode() + b"\0")
        fingerprint.update(digest.digest())
    listing = snapshot / "sources.txt"
    return {
        "revision": revision,
        "build": manifest["build"],
        "source_ids": sorted(manifest["sources"]),
        "counts": manifest["inspection"]["counts"],
        "fingerprint": fingerprint.hexdigest(),
        "extraction_source_ids": (
            [str(uuid.UUID(Path(line).stem)) for line in listing.read_text().splitlines()]
            if listing.exists()
            else []
        ),
        "has_extraction_listing": listing.exists(),
    }


@pytest.fixture
def read_snapshot():
    directory = os.environ.get("INSPIRATION_WISE_DATA_DIR")
    container = os.environ.get("INSPIRATION_WISE_CONTAINER")
    assert bool(directory) != bool(container), (
        "Set exactly one snapshot reader: INSPIRATION_WISE_DATA_DIR or INSPIRATION_WISE_CONTAINER"
    )
    if directory:
        return lambda project_id, revision: snapshot_observation(directory, project_id, revision)
    python = os.environ.get(
        "INSPIRATION_WISE_CONTAINER_PYTHON", "/opt/conda/envs/wise-env/bin/python"
    )
    script = (
        "import os, sys, json\n"
        + inspect.getsource(snapshot_observation)
        + "\nprint(json.dumps(snapshot_observation("
        "os.environ.get('WISE_BRIDGE_DATA', '/data'), sys.argv[1], sys.argv[2])))\n"
    )

    def read(project_id, revision):
        # Only UUID arguments and a pure reader enter the explicitly chosen
        # existing container. Tokens are never sent to exec or diagnostics.
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                python,
                "-c",
                script,
                str(uuid.UUID(str(project_id))),
                str(uuid.UUID(str(revision))),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, "Read-only snapshot inspection failed"
        return json.loads(result.stdout)

    return read


def test_real_incremental_upserts_and_delete_reuse_features(
    media_studio,
    changing_video,
    monkeypatch,
    read_snapshot,
    record_property,
):
    client, sessions, ids = media_studio
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual"])
    remote = WiseClient(settings.library_wise_url, settings.library_wise_token)
    health = remote.health()
    assert health.get("worker_alive") is True
    assert health.get("capabilities", {}).get("visual", {}).get("configured") is True
    timeout = int(os.environ.get("INSPIRATION_WISE_TIMEOUT_SECONDS", "600"))
    assert 30 <= timeout <= 3600
    project_id = ids["project"]
    base = f"/api/v1/projects/{project_id}/library"
    picture = io.BytesIO()
    Image.new("RGB", (360, 240), "royalblue").save(picture, format="PNG")
    payloads = [
        (changing_video, "first.mp4"),
        (picture.getvalue(), "middle.png"),
        (changing_video, "last.mp4"),
    ]
    versions, tracked, observations = [], [], []
    previous = None
    succeeded = False
    try:
        for index, (content, name) in enumerate(payloads):
            version = upload_media(client, base, content, name)
            run = post_ok(
                client,
                f"{base}/versions/{version['id']}/process",
                {"provider": "wise", "capabilities": ["visual"]},
            )
            # Track before submitting to the actual remote worker, including a
            # request accepted remotely whose local HTTP response might fail.
            tracked.append((version["id"], run["id"]))
            started = time.monotonic()
            completed = wait_local(client, base, run["id"], timeout)
            assert completed["capability_status"]["visual"]["vector_count"] > 0
            job = remote.get_job(run["id"])
            assert job and job["status"] == "ready" and job["project_id"] == project_id
            observation = read_snapshot(project_id, job["revision"])
            versions.append(version)
            assert observation["source_ids"] == sorted(item["id"] for item in versions)
            assert observation["build"] == {"reused_sources": index, "extracted_sources": 1}
            assert observation["extraction_source_ids"] == [version["id"]]
            assert all(counts["visual"] > 0 for counts in observation["counts"].values())
            if previous:
                retained = read_snapshot(project_id, previous["revision"])
                assert retained == previous, "Upsert mutated its published input snapshot"
            previous = observation
            observations.append(
                {"operation": "upsert", "seconds": time.monotonic() - started, **observation}
            )

        query = {
            "engine": "wise",
            "target": "visual",
            "query": "蓝色画面",
            "limit": 100,
            "collapse_versions": False,
        }
        all_hits = post_ok(client, f"{base}/search", query)
        assert all_hits["coverage"]["indexed"] == 3
        assert {hit["version_id"] for hit in all_hits["items"]} == {
            version["id"] for version in versions
        }
        image_hits = [hit for hit in all_hits["items"] if hit["version_id"] == versions[1]["id"]]
        assert image_hits
        assert all(
            hit["match_type"] == "image" and hit["start_ms"] is None and hit["end_ms"] is None
            for hit in image_hits
        )

        deleted = versions[1]
        post_ok(client, f"{base}/{deleted['media_id']}/trash")
        # Business tombstones must hide results even before remote cleanup starts.
        assert deleted["id"] not in {
            hit["version_id"] for hit in post_ok(client, f"{base}/search", query)["items"]
        }
        rows = client.get(f"{base}/processing/runs?limit=100")
        assert rows.status_code == 200
        cleanup = next(row for row in rows.json()["items"] if row["operation"] == "delete")
        started = time.monotonic()
        wait_local(client, base, cleanup["id"], timeout)
        remote_cleanup = remote.get_job(cleanup["id"])
        assert remote_cleanup and remote_cleanup["status"] == "ready"
        after_delete = read_snapshot(project_id, remote_cleanup["revision"])
        remaining = [versions[0], versions[2]]
        assert after_delete["source_ids"] == sorted(item["id"] for item in remaining)
        assert after_delete["build"] == {"reused_sources": 2, "extracted_sources": 0}
        assert after_delete["has_extraction_listing"] is False
        assert after_delete["extraction_source_ids"] == []
        assert all(counts["visual"] > 0 for counts in after_delete["counts"].values())
        observations.append(
            {"operation": "delete", "seconds": time.monotonic() - started, **after_delete}
        )

        for media_query in [
            query,
            {key: value for key, value in query.items() if key != "query"}
            | {"image": "data:image/png;base64," + base64.b64encode(picture.getvalue()).decode()},
        ]:
            result = post_ok(client, f"{base}/search", media_query)
            assert result["coverage"]["indexed"] == 2
            assert {hit["version_id"] for hit in result["items"]} == {
                version["id"] for version in remaining
            }
            for hit in result["items"]:
                version = next(item for item in remaining if item["id"] == hit["version_id"])
                assert hit["media_type"] == "video" and hit["match_type"] == "frame"
                assert 0 <= hit["start_ms"] < hit["end_ms"] <= version["duration_ms"]
                assert math.isfinite(hit["score"])
        for version in remaining:
            scoped = remote.search(
                project_id,
                {
                    "query": "blue",
                    "modality": "visual",
                    "source_ids": [version["id"]],
                    "limit": 100,
                },
            )
            assert scoped["items"]
            assert {hit["source_id"] for hit in scoped["items"]} == {version["id"]}
        assert (
            remote.search(
                project_id,
                {
                    "query": "blue",
                    "modality": "visual",
                    "source_ids": [deleted["id"]],
                    "limit": 100,
                },
            )["items"]
            == []
        )
        succeeded = True
    finally:
        record_property("wise_incremental_observations", json.dumps(observations))
        errors = []
        for source_id, anchor in tracked:
            try:
                known_anchor = anchor if remote.get_job(anchor) else None
                job = remote.delete(project_id, source_id, uuid.uuid4(), source_job_id=known_anchor)
                assert wait_remote(remote, job["job_id"], timeout)["status"] == "ready"
            except Exception as exc:
                errors.append(type(exc).__name__)
        if errors:
            message = "Incremental live WISE cleanup failed: " + ", ".join(errors)
            if succeeded:
                pytest.fail(message)
            warnings.warn(message, RuntimeWarning, stacklevel=1)
