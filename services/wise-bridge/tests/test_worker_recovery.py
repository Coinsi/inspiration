"""Filesystem/kill recovery checks; no ML models or runtime services are used."""

# ruff: noqa: F811

import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from test_bridge import identifiers, service, upload, work  # noqa: F401
from wise_bridge.runner import EngineFailure
from wise_bridge.store import Store


def wait_terminal(store, job):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = store.job(job)
        if result["state"] in {"ready", "failed", "canceled"}:
            return result
        time.sleep(0.01)
    pytest.fail("Worker did not finish the queued task")


def test_log_and_cleanup_errors_do_not_kill_queue_worker(service, monkeypatch):
    client, store, worker, engine = service
    project, source, first = identifiers()
    second = str(uuid.uuid4())
    upload(client, project, source, first)
    upload(client, project, str(uuid.uuid4()), second)
    original_build = engine.build
    builds = []

    def build(sources, snapshot, *args):
        builds.append(snapshot)
        result = original_build(sources, snapshot, *args)
        (snapshot / "engine.log").write_text("private engine diagnostics")
        if len(builds) == 1:
            raise EngineFailure("An extraction failed")
        return result

    engine.build = build
    import wise_bridge.runner as runner_module

    copyfile, rmtree = runner_module.shutil.copyfile, runner_module.shutil.rmtree
    failures = {"log": False, "cleanup": False}

    def fail_log_once(source, destination, *args, **kwargs):
        if not failures["log"]:
            failures["log"] = True
            raise OSError("Simulated full log disk")
        return copyfile(source, destination, *args, **kwargs)

    def fail_cleanup_once(path, *args, **kwargs):
        if builds and Path(path) == builds[0] and not failures["cleanup"]:
            failures["cleanup"] = True
            raise OSError("Simulated temporarily locked snapshot")
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runner_module.shutil, "copyfile", fail_log_once)
    monkeypatch.setattr(runner_module.shutil, "rmtree", fail_cleanup_once)
    worker.start()
    try:
        assert wait_terminal(store, first)["state"] == "failed"
        assert wait_terminal(store, second)["state"] == "ready"
        assert worker.thread.is_alive()
        assert all(failures.values())
        assert not builds[0].exists()
    finally:
        worker.stop()


def test_uncaught_job_error_and_temporary_db_error_reconcile_before_next_claim(
    service, monkeypatch
):
    client, store, worker, _ = service
    project, source, first = identifiers()
    second = str(uuid.uuid4())
    upload(client, project, source, first)
    upload(client, project, str(uuid.uuid4()), second)
    process, finish = worker.process, store.finish
    failed_write = False

    def unexpected_failure(job):
        if job["id"] == first:
            raise OSError("Unexpected filesystem failure outside task handler")
        assert store.job(first)["state"] == "failed"
        process(job)

    def unavailable_once(job, state, error=None):
        nonlocal failed_write
        if job == first and not failed_write:
            failed_write = True
            raise sqlite3.OperationalError("Simulated unavailable database")
        return finish(job, state, error)

    monkeypatch.setattr(worker, "process", unexpected_failure)
    monkeypatch.setattr(store, "finish", unavailable_once)
    worker.start()
    try:
        assert wait_terminal(store, second)["state"] == "ready"
        assert store.job(first)["state"] == "failed"
        assert store.job(first)["error"] == "WISE worker failed; retry the task"
        assert failed_write and worker.thread.is_alive()
    finally:
        worker.stop()


def test_hard_exit_partial_is_reclaimed_before_capacity_check_without_deleting_heads(
    service, tmp_path
):
    client, store, worker, engine = service
    project, source, first = identifiers()
    upload(client, project, source, first)
    work(store, worker)
    old_revision = store.head(project)
    upload(client, project, str(uuid.uuid4()), str(uuid.uuid4()))
    work(store, worker)
    head = store.head(project)
    snapshots = tmp_path / "projects" / project / "snapshots"
    unknown = snapshots / str(uuid.uuid4())
    unknown.mkdir()
    (unknown / "sentinel").write_text("Not registered to this worker")
    failed_job = str(uuid.uuid4())
    upload(client, project, str(uuid.uuid4()), failed_job)
    script = """
import os,sys
from pathlib import Path
from wise_bridge.config import Settings
from wise_bridge.store import Store
from wise_bridge.runner import Worker
class KilledEngine:
    def operation(self,*args,**kwargs): return {}
    def build(self,sources,snapshot,*args):
        snapshot.mkdir(parents=True)
        (snapshot/'incomplete.features').write_bytes(b'x'*(2*1024**2))
        os._exit(73)
root=Path(sys.argv[1])
store=Store(root)
Worker(Settings(data_dir=root,token='test-worker-token-more-than-24',min_free_bytes=0),store,KilledEngine()).process(store.claim())
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    partial = snapshots / store.snapshots(unpublished=True)[0]["revision"]
    assert partial.exists() and store.job(failed_job)["state"] == "processing"
    store.recover()
    worker.settings = replace(worker.settings, max_storage_bytes=1024**2)
    engine.fail = True

    def assert_cleanup_before_build():
        assert not partial.exists()
        assert (snapshots / head).exists()
        assert (snapshots / old_revision).exists()
        assert (unknown / "sentinel").read_text() == "Not registered to this worker"

    engine.on_build = assert_cleanup_before_build
    assert work(store, worker)["state"] == "failed"
    assert store.head(project) == head
    assert not store.snapshots(unpublished=True)
    engine.fail = False
    store.retry(failed_job)
    assert work(store, worker)["state"] == "ready"
    assert (snapshots / head).exists() and unknown.exists()


def test_empty_delete_can_release_storage_without_reserving_old_index_copy(service):
    client, store, worker, engine = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    snapshot = (
        worker.settings.data_dir
        / "projects"
        / project
        / "snapshots"
        / store.head(project)
    )
    (snapshot / "large.features").write_bytes(b"x" * (2 * 1024**2))
    worker.settings = replace(worker.settings, max_storage_bytes=1024**2)
    engine.operation = lambda *args, **kwargs: pytest.fail(
        "Empty deletion must not launch a WISE probe under storage pressure"
    )
    delete_job = str(uuid.uuid4())
    assert (
        client.delete(
            f"/v1/projects/{project}/sources/{source}", params={"job_id": delete_job}
        ).status_code
        == 202
    )
    assert work(store, worker)["state"] == "ready"
    assert not snapshot.exists()
    assert not store.sources(project)


def test_snapshot_registry_preserves_head_even_if_marker_is_incomplete(service):
    client, store, worker, _ = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    head = store.head(project)
    with store.connect() as conn:
        conn.execute("UPDATE snapshots SET published=0 WHERE revision=?", (head,))
    worker._cleanup_unpublished_snapshots()
    path = worker.settings.data_dir / "projects" / project / "snapshots" / head
    assert json.loads((path / "manifest.json").read_text())["sources"]
    assert store.head(project) == head


def test_claim_does_not_need_a_post_commit_database_read(service, monkeypatch):
    client, store, _, _ = service
    project, source, job = identifiers()
    upload(client, project, source, job)

    def read_would_fail(*args):
        raise sqlite3.OperationalError("A new connection cannot be opened")

    monkeypatch.setattr(store, "job", read_would_fail)
    claimed = store.claim()
    assert claimed["id"] == job and claimed["state"] == "processing"
    assert claimed["attempts"] == 1


def test_oversized_index_never_replaces_queryable_head(service):
    client, store, worker, engine = service
    project, source, first = identifiers()
    upload(client, project, source, first)
    work(store, worker)
    head = store.head(project)
    second_source, second = str(uuid.uuid4()), str(uuid.uuid4())
    upload(client, project, second_source, second)
    original_build = engine.build

    def oversized_build(sources, snapshot, *args):
        result = original_build(sources, snapshot, *args)
        (snapshot / "engine" / "visual.faiss").write_bytes(b"x" * 2048)
        return result

    engine.build = oversized_build
    worker.settings = replace(worker.settings, query_cache_index_bytes=1024)
    failed = work(store, worker)
    assert failed["state"] == "failed"
    assert "WISE_QUERY_CACHE_INDEX_BYTES" in failed["error"]
    assert store.head(project) == head
    assert set(store.sources(project)) == {source}
    response = client.post(f"/v1/projects/{project}/search", json={"query": "rain"})
    assert response.status_code == 200
    assert [item["source_id"] for item in response.json()["items"]] == [source]
    assert not store.snapshots(unpublished=True)


def test_legacy_snapshot_migration_uses_published_job_evidence_only(service):
    client, store, worker, _ = service
    project, source, first = identifiers()
    upload(client, project, source, first)
    work(store, worker)
    first_revision = store.head(project)
    upload(client, project, str(uuid.uuid4()), str(uuid.uuid4()))
    work(store, worker)
    second_revision = store.head(project)
    snapshots = worker.settings.data_dir / "projects" / project / "snapshots"
    unknown = snapshots / str(uuid.uuid4())
    unknown.mkdir()
    (unknown / "unowned").write_text("Retain for manual review")
    with store.connect() as conn:
        conn.execute("DROP TABLE snapshots")
    migrated = Store(worker.settings.data_dir)
    worker.store = migrated
    worker._cleanup_unpublished_snapshots()
    assert (snapshots / first_revision).exists()
    assert (snapshots / second_revision).exists()
    assert unknown.exists()
    upload(client, project, str(uuid.uuid4()), str(uuid.uuid4()))
    assert work(migrated, worker)["state"] == "ready"
    assert not (snapshots / first_revision).exists()
    assert (snapshots / second_revision).exists()
    assert (unknown / "unowned").read_text() == "Retain for manual review"
    reopened = Store(worker.settings.data_dir)
    assert first_revision not in {
        record["revision"] for record in reopened.snapshots(project_id=project)
    }
