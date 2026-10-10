"""Durable restoration/deletion generations, including real SQLite races."""

# ruff: noqa: F811

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_bridge import identifiers, service, upload, work  # noqa: F401

from wise_bridge.store import Conflict, Store


def new_id():
    return str(uuid.uuid4())


def restore(client, project, source, job, deletion, body=b"test proxy bytes"):
    return client.post(
        f"/v1/projects/{project}/sources/{source}",
        data={
            "job_id": job,
            "media_type": "video",
            "capabilities": '["visual","audio"]',
            "restore_after_job_id": deletion,
        },
        files={"file": ("proxy.mp4", body, "video/mp4")},
    )


def delete(client, project, source, job, anchor=None):
    params = {"job_id": job}
    if anchor:
        params["source_job_id"] = anchor
    return client.delete(f"/v1/projects/{project}/sources/{source}", params=params)


def test_restore_waits_for_cleanup_and_new_index_then_rejects_old_delete_replays(
    service,
):
    client, store, worker, _ = service
    project, source, original = identifiers()
    upload(client, project, source, original)
    work(store, worker)
    deletion = new_id()
    assert delete(client, project, source, deletion).status_code == 202
    assert restore(client, project, source, new_id(), deletion).status_code == 409
    work(store, worker)
    restored = new_id()
    assert (
        restore(client, project, source, restored, deletion).json()["generation"] == 1
    )
    assert not store.sources(project)
    query = lambda: client.post(
        f"/v1/projects/{project}/search", json={"query": "rain"}
    ).json()["items"]
    assert query() == []
    work(store, worker)
    assert query()
    assert delete(client, project, source, deletion).json()["status"] == "ready"
    assert delete(client, project, source, new_id(), original).status_code == 409
    assert delete(client, project, source, new_id()).status_code == 409
    # A delayed initial-generation upload cannot silently inherit a new generation.
    assert upload(client, project, source, new_id()).status_code == 409
    assert upload(client, project, source, original).json()["generation"] == 0
    assert query()
    # Another capability job with the same restore marker remains in generation 1.
    assert (
        restore(client, project, source, new_id(), deletion).json()["generation"] == 1
    )
    work(store, worker)
    assert delete(client, project, source, new_id(), restored).status_code == 202
    assert query() == []


def test_restore_requires_latest_successful_cleanup_and_old_failed_retry_is_blocked(
    service,
):
    client, store, worker, engine = service
    project, source, original = identifiers()
    upload(client, project, source, original)
    work(store, worker)
    first = new_id()
    delete(client, project, source, first)
    engine.fail = True
    assert work(store, worker)["state"] == "failed"
    assert restore(client, project, source, new_id(), first).status_code == 409
    latest = new_id()
    delete(client, project, source, latest)
    engine.fail = False
    work(store, worker)
    assert restore(client, project, source, new_id(), first).status_code == 409
    assert client.post(f"/v1/jobs/{first}/retry").status_code == 202
    assert restore(client, project, source, new_id(), latest).status_code == 409
    engine.fail = True
    work(store, worker)
    engine.fail = False
    assert restore(client, project, source, new_id(), latest).status_code == 202
    assert client.post(f"/v1/jobs/{first}/retry").status_code == 409


def test_deleted_version_keeps_hash_immutable_without_installing_rejected_file(service):
    client, store, worker, _ = service
    project, source, original = identifiers()
    upload(client, project, source, original)
    work(store, worker)
    path = Path(store.sources(project)[source]["file"])
    deletion = new_id()
    delete(client, project, source, deletion)
    work(store, worker)
    assert not path.exists()
    assert (
        restore(client, project, source, new_id(), deletion, b"changed").status_code
        == 409
    )
    assert not path.exists()
    assert restore(client, project, source, new_id(), deletion).status_code == 202
    assert path.read_bytes() == b"test proxy bytes"


def test_unknown_source_tombstone_blocks_delayed_upload_until_explicit_restore(service):
    client, store, worker, _ = service
    project, source, deletion = identifiers()
    delete(client, project, source, deletion)
    work(store, worker)
    assert upload(client, project, source, new_id()).status_code == 409
    assert (
        restore(client, project, source, new_id(), deletion).json()["generation"] == 1
    )
    assert not store.sources(project)
    work(store, worker)
    assert source in store.sources(project)


def setup_deleted_store(tmp_path):
    store = Store(tmp_path)
    project, source, original = identifiers()
    payload = {"sha256": "immutable", "file": "proxy", "capabilities": ["visual"]}
    store.enqueue(original, project, source, "upsert", payload)
    store.publish(store.claim(), "initial", {})
    failed = new_id()
    store.enqueue(failed, project, source, "delete", {})
    store.finish(store.claim()["id"], "failed")
    latest = new_id()
    store.enqueue(latest, project, source, "delete", {})
    store.publish(store.claim(), "deleted", {})
    return store, project, source, payload, failed, latest


@pytest.mark.parametrize("iteration", range(6))
def test_restore_and_old_delete_retry_serialize_in_sqlite(tmp_path, iteration):
    store, project, source, payload, failed, latest = setup_deleted_store(tmp_path)
    barrier = threading.Barrier(2)

    def restoring():
        barrier.wait()
        try:
            store.enqueue(
                new_id(),
                project,
                source,
                "upsert",
                payload,
                restore_after_job_id=latest,
            )
            return "restored"
        except Conflict:
            return "restore_blocked"

    def retrying():
        barrier.wait()
        try:
            store.retry(failed)
            return "retried"
        except Conflict:
            return "retry_blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(restoring), pool.submit(retrying)
        outcomes = {a.result(timeout=10), b.result(timeout=10)}
    assert outcomes in ({"restored", "retry_blocked"}, {"retried", "restore_blocked"})
    assert not store.sources(project)


def test_old_generation_cannot_publish_over_restored_head(tmp_path):
    store, project, source, payload, failed, latest = setup_deleted_store(tmp_path)
    old = store.job(failed)
    store.enqueue(
        new_id(), project, source, "upsert", payload, restore_after_job_id=latest
    )
    store.publish(store.claim(), "restored", {})
    with store.connect() as conn:
        conn.execute("UPDATE jobs SET state='processing' WHERE id=?", (failed,))
    assert not store.publish(old, "stale-delete", {})
    assert store.head(project) == "restored"
    assert source in store.sources(project)
    assert store.job(failed)["state"] == "canceled"


def test_reopening_store_preserves_generation_and_restore_preconditions(tmp_path):
    store, project, source, payload, _, latest = setup_deleted_store(tmp_path)
    job = new_id()
    store.enqueue(job, project, source, "upsert", payload, restore_after_job_id=latest)
    reopened = Store(tmp_path)
    assert reopened.job(job)["generation"] == 1
    with pytest.raises(Conflict, match="precondition"):
        reopened.enqueue(new_id(), project, source, "delete", {})
    reopened.publish(reopened.claim(), "restored", {})
    assert source in reopened.sources(project)


def test_restore_adopted_before_old_cleanup_keeps_its_proxy(service):
    client, store, worker, _ = service
    project, source, original = identifiers()
    upload(client, project, source, original)
    work(store, worker)
    path = Path(store.sources(project)[source]["file"])
    deletion = new_id()
    delete(client, project, source, deletion)
    original_cleanup = worker._cleanup_deleted_sources
    restored = new_id()

    def restore_before_file_cleanup(project_id):
        assert restore(client, project, source, restored, deletion).status_code == 202
        original_cleanup(project_id)
        assert path.exists()

    worker._cleanup_deleted_sources = restore_before_file_cleanup
    assert work(store, worker)["state"] == "ready"
    worker._cleanup_deleted_sources = original_cleanup
    assert not store.sources(project)
    assert work(store, worker)["state"] == "ready"
    assert path.exists()


def test_existing_sqlite_schema_migrates_deleted_sources_safely(tmp_path):
    store, project, source, payload, failed, latest = setup_deleted_store(tmp_path)
    with store.connect() as conn:
        for table, names in {
            "jobs": ("generation", "restore_after_job_id", "source_job_id"),
            "sources": ("generation", "last_delete_job_id", "restore_after_job_id"),
        }.items():
            for name in names:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
    migrated = Store(tmp_path)
    assert migrated.job(failed)["generation"] == 0
    result = migrated.enqueue(
        new_id(), project, source, "upsert", payload, restore_after_job_id=latest
    )
    assert result["generation"] == 1
    assert not migrated.sources(project)


def test_deletion_anchor_cannot_cross_project_or_source(service):
    client, store, worker, _ = service
    project, source, original = identifiers()
    upload(client, project, source, original)
    work(store, worker)
    other_project, other_source, other_job = identifiers()
    upload(client, other_project, other_source, other_job)
    work(store, worker)
    assert delete(client, project, source, new_id(), other_job).status_code == 409
    assert source in store.sources(project)


def test_cleanup_retains_file_adopted_by_pending_capability_job(service):
    client, store, worker, _ = service
    project, source, original = identifiers()
    upload(client, project, source, original)
    work(store, worker)
    old_file = Path(store.sources(project)[source]["file"])
    new_job = new_id()
    response = client.post(
        f"/v1/projects/{project}/sources/{source}",
        data={"job_id": new_job, "media_type": "video", "capabilities": '["visual"]'},
        files={"file": ("same-bytes.webm", b"test proxy bytes", "video/webm")},
    )
    assert response.status_code == 202
    pending_file = Path(store.job(new_job)["payload"]["file"])
    assert pending_file != old_file
    worker._cleanup_deleted_sources(project)
    assert old_file.exists() and pending_file.exists()
