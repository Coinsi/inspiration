"""EXPLICITLY SCHEDULED real WISE cancellation and container SIGKILL acceptance.

Default skip. Requires INSPIRATION_WISE_RECOVERY_E2E=1 and the normal live test
URL/token/TEST_DATABASE_URL; INSPIRATION_WISE_CONTAINER plus
INSPIRATION_MEDIACMS_CONTAINER; a timezone-aware INSPIRATION_WISE_EXCLUSIVE_UNTIL
(within two hours), and INSPIRATION_WISE_EXCLUSIVE_ACK=non-test-work-is-idle.
The hard-kill case additionally requires INSPIRATION_WISE_FAULT_CONTAINER_ID to
equal the exact full Docker ID. It never builds, pulls, replaces or deletes a
container/volume. Only its own isolated project sources are changed/cleaned.

The benchmark filesystem lock is held for the entire test. WISE global jobs,
main application queues and MediaCMS DB/Celery must be idle outside this project
at admission and immediately before fault injection. This supplements, rather
than replaces, an operator's exclusive window: unrelated apps cannot be frozen
atomically by a test. No BaseException/mock state is counted as a real kill.
"""

# ruff: noqa: F811
import json
import os
import time
import uuid
import warnings
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.media_processing import MediaProcessingRun
from app.modules.generation import media_engine
from app.modules.library import processing_worker, worker
from app.modules.library.providers.wise import WiseClient
from scripts.benchmark_media_pipeline import exclusive_run
from tests.test_media_workflow import changing_video, post_ok  # noqa: F401
from tests.test_media_workflow import studio as media_studio  # noqa: F401
from tests.test_wise_incremental_live import read_snapshot  # noqa: F401
from tests.test_wise_live import live_settings, wait_local, wait_remote  # noqa: F401
from tests.test_wise_multimodal_live import upload_media
from tests.wise_fault_support import ContainerAccess, require_window

pytestmark = pytest.mark.skipif(
    os.environ.get("INSPIRATION_WISE_RECOVERY_E2E") != "1",
    reason="Live WISE fault tests need an explicit exclusive window and opt-in",
)
REPO = Path(__file__).resolve().parents[2]


def until(check, timeout=30, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError("Live WISE fault acceptance exceeded its bounded deadline")


@pytest.fixture(autouse=True)
def fault_access(live_settings, request, monkeypatch):
    require_window(os.environ)
    container = os.environ.get("INSPIRATION_WISE_CONTAINER")
    cms_container = os.environ.get("INSPIRATION_MEDIACMS_CONTAINER")
    assert container and cms_container, "Explicit WISE and MediaCMS containers are required"
    assert not os.environ.get("INSPIRATION_WISE_DATA_DIR"), (
        "Fault tests inspect the chosen container"
    )
    # The existing benchmark lock prevents this test overlapping independently
    # scheduled CMS/WISE load tests, including their non-public database schemas.
    with exclusive_run(REPO / ".runtime" / "media-benchmark"):
        access = ContainerAccess(container, cms_container)
        if (
            request.node.name
            == "test_real_container_sigkill_resumes_same_job_without_duplicate_sources"
        ):
            require_window(os.environ, access.container)
        monkeypatch.setenv("INSPIRATION_WISE_CONTAINER", access.container)
        access.require_idle(str(uuid.UUID(int=0)))
        yield access


@pytest.fixture
def recovery_case(media_studio, monkeypatch, fault_access, record_property):
    client, sessions, ids = media_studio
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    monkeypatch.setattr(processing_worker, "engine", sessions.kw["bind"])
    monkeypatch.setattr(processing_worker, "QUEUE_LOCK", uuid.uuid4().int % (2**62))
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_processing_auto", False)
    monkeypatch.setattr(settings, "library_processing_poll_seconds", 2)
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual"])
    remote = WiseClient(settings.library_wise_url, settings.library_wise_token)
    assert remote.health().get("worker_alive") is True
    timeout = int(os.environ.get("INSPIRATION_WISE_TIMEOUT_SECONDS", "900"))
    assert 120 <= timeout <= 3600
    case = {
        "client": client,
        "sessions": sessions,
        "project": ids["project"],
        "base": f"/api/v1/projects/{ids['project']}/library",
        "remote": remote,
        "access": fault_access,
        "timeout": timeout,
        "tracked": [],
        "evidence": {
            "test_run": uuid.uuid4().hex,
            "observations": [],
            "body_passed": False,
            "cleanup_complete": False,
            "passed": False,
        },
    }
    try:
        yield case
    finally:
        errors = []
        for source_id, anchor in case["tracked"]:
            try:
                current = remote.get_job(anchor)
                if current and current["status"] in ("queued", "processing"):
                    remote.cancel(anchor)
                    wait_remote(remote, anchor, timeout)
                cleanup = remote.delete(
                    case["project"],
                    source_id,
                    uuid.uuid4(),
                    source_job_id=anchor if current else None,
                )
                assert wait_remote(remote, cleanup["job_id"], timeout)["status"] == "ready"
            except Exception as exc:
                errors.append(type(exc).__name__)
        if not errors:
            try:
                final = fault_access.observe(case["project"])
                case["evidence"]["cleanup"] = final
                assert not final["source_files"] and not any(
                    row["active"] for row in final["sources"]
                )
                assert not any(row["project_id"] == case["project"] for row in final["active_jobs"])
                assert not any(row["published"] == 0 for row in final["snapshots"])
            except Exception as exc:
                errors.append(type(exc).__name__)
        case["evidence"]["cleanup_complete"] = not errors
        case["evidence"]["cleanup_errors"] = errors
        case["evidence"]["passed"] = (
            case["evidence"]["body_passed"] and case["evidence"]["cleanup_complete"]
        )
        target = REPO / ".runtime" / "media-integration" / "wise-faults"
        target.mkdir(parents=True, exist_ok=True)
        (target / (case["evidence"]["test_run"] + ".json")).write_text(
            json.dumps(case["evidence"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        record_property("wise_fault_evidence", json.dumps(case["evidence"]))
        if errors:
            message = "Owned WISE fault-test cleanup did not complete: " + ", ".join(errors)
            if case["evidence"]["body_passed"]:
                pytest.fail(message)
            warnings.warn(message, RuntimeWarning, stacklevel=1)


@pytest.fixture
def long_video(tmp_path, fault_access):
    duration = int(os.environ.get("INSPIRATION_WISE_FAULT_VIDEO_SECONDS", "180"))
    assert 60 <= duration <= 900
    source = tmp_path / "fault-target.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=640x360:rate=12:duration={duration}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            str(source),
        ]
    )
    assert source.stat().st_size <= 64 * 1024 * 1024
    return source.read_bytes(), duration


def submit(case, content, name):
    version = upload_media(case["client"], case["base"], content, name)
    run = post_ok(
        case["client"],
        f"{case['base']}/versions/{version['id']}/process",
        {
            "provider": "wise",
            "capabilities": ["visual"],
        },
    )
    case["tracked"].append((version["id"], run["id"]))
    return version, run


def query(case, versions):
    result = case["remote"].search(
        case["project"],
        {
            "query": "blue",
            "modality": "visual",
            "source_ids": list(versions),
            "limit": 100,
        },
    )
    assert result["items"]
    assert {hit["source_id"] for hit in result["items"]}.issubset(set(versions))
    return result


def baseline(case, content, read_snapshot):
    version, run = submit(case, content, "baseline.mp4")
    wait_local(case["client"], case["base"], run["id"], case["timeout"])
    job = case["remote"].get_job(run["id"])
    snapshot = read_snapshot(case["project"], job["revision"])
    assert snapshot["source_ids"] == [version["id"]]
    query(case, [version["id"]])
    case["evidence"]["baseline"] = snapshot
    return version, snapshot


def wait_extracting(case, run):
    processing_worker.process(uuid.UUID(run["id"]))  # actual bridge submission

    def observed():
        state = case["remote"].get_job(run["id"])
        assert state and state["status"] not in ("ready", "failed", "canceled"), (
            "Fault checkpoint was missed; increase the owned fixture duration and rerun"
        )
        if state["status"] != "processing" or state["phase"] != "extracting":
            return None
        observation = case["access"].observe(case["project"])
        return observation if observation["owned_processes"] else None

    return until(observed, timeout=case["timeout"], interval=0.1)


def test_running_cancel_preserves_head_stops_children_and_releases_slot(
    recovery_case,
    changing_video,
    long_video,
    read_snapshot,
):
    case = recovery_case
    first, old = baseline(case, changing_video, read_snapshot)
    target, run = submit(case, long_video[0], "cancel-target.mp4")
    assert target["duration_ms"] >= 59000
    case["access"].require_idle(case["project"])
    running = wait_extracting(case, run)
    assert running["head"] == old["revision"]
    groups = {row["pgid"] for row in running["owned_processes"]}
    require_window(os.environ)
    post_ok(case["client"], f"{case['base']}/processing/runs/{run['id']}/cancel")
    processing_worker.process(uuid.UUID(run["id"]))  # real cancel API, not a mock return
    assert wait_remote(case["remote"], run["id"], 30)["status"] == "canceled"

    def quiescent():
        observed = case["access"].observe(case["project"])
        return (
            observed
            if not (groups & set(observed["live_groups"]))
            and not any(row["published"] == 0 for row in observed["snapshots"])
            else None
        )

    stopped = until(quiescent)
    assert stopped["head"] == old["revision"]
    assert read_snapshot(case["project"], old["revision"]) == old
    result = query(case, [first["id"], target["id"]])
    assert result["revision"] == old["revision"]
    assert {item["source_id"] for item in result["items"]} == {first["id"]}
    with case["sessions"]() as db:
        local = db.get(MediaProcessingRun, uuid.UUID(run["id"]))
        assert local.status == "canceled" and local.next_poll_at is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(MediaProcessingRun)
                .where(MediaProcessingRun.status.in_(("queued", "processing", "waiting")))
            )
            == 0
        )
    sentinel, next_run = submit(case, changing_video, "after-cancel.mp4")

    def scheduled():
        processing_worker.run_once()  # exercise the actual persisted provider-slot dispatcher
        with case["sessions"]() as db:
            row = db.get(MediaProcessingRun, uuid.UUID(next_run["id"]))
            assert row.status not in ("failed", "canceled")
            return row.status == "ready"

    until(scheduled, timeout=case["timeout"], interval=0.5)
    assert query(case, [sentinel["id"]])["items"]
    case["evidence"].update(
        {
            "case": "running_cancel",
            "duration_seconds": long_video[1],
            "observations": [running, stopped],
            "slot_released_job": next_run["id"],
            "body_passed": True,
        }
    )


def test_real_container_sigkill_resumes_same_job_without_duplicate_sources(
    recovery_case,
    changing_video,
    long_video,
    read_snapshot,
):
    case = recovery_case
    require_window(
        os.environ, case["access"].container
    )  # reject missing kill opt-in before any work
    first, old = baseline(case, changing_video, read_snapshot)
    target, run = submit(case, long_video[0], "restart-target.mp4")
    running = wait_extracting(case, run)
    attempts = case["remote"].get_job(run["id"])["attempts"]
    assert running["head"] == old["revision"]
    fault = case["access"].kill_and_restore(case["project"], run["id"])

    def healthy():
        try:
            return case["remote"].health().get("worker_alive") is True
        except Exception:
            return False

    until(healthy, timeout=90, interval=1)
    wait_local(case["client"], case["base"], run["id"], case["timeout"])
    completed = case["remote"].get_job(run["id"])
    assert completed["job_id"] == run["id"] and completed["attempts"] > attempts
    assert completed["status"] == "ready"
    after = case["access"].observe(case["project"])
    assert len([row for row in after["jobs"] if row["id"] == run["id"]]) == 1
    assert len(after["sources"]) == 2 and all(row["active"] for row in after["sources"])
    assert len(after["source_files"]) == 2
    assert not any(row["published"] == 0 for row in after["snapshots"])
    assert set(after["snapshot_directories"]) == {row["revision"] for row in after["snapshots"]}
    assert read_snapshot(case["project"], old["revision"]) == old
    new = read_snapshot(case["project"], completed["revision"])
    assert new["source_ids"] == sorted([first["id"], target["id"]])
    assert new["build"] == {"reused_sources": 1, "extracted_sources": 1}
    for version in (first, target):
        assert query(case, [version["id"]])["items"]
    case["evidence"].update(
        {
            "case": "container_sigkill",
            "duration_seconds": long_video[1],
            "observations": [running, fault, after],
            "completed_job": completed,
            "new_snapshot": new,
            "body_passed": True,
        }
    )
