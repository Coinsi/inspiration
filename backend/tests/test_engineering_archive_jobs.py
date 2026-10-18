"""Persistent archive API, worker recovery and authorized range downloads."""

# ruff: noqa: F811
import uuid

from sqlalchemy import text

from app.models.engineering import EngineeringArchiveJob
from app.modules.engineering import archive_jobs, archives
from tests.test_engineering import accept
from tests.test_engineering_review import draft, preview, ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_background_export_retry_restart_and_range_download(studio, monkeypatch):
    client, sessions, ids, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    accept(client, path, change)
    key = uuid.uuid4()
    job = post_ok(client, path + f"/archive-jobs?request_key={key}", {})
    assert job["status"] == "pending" and job["revision"] == 1
    assert post_ok(client, path + f"/archive-jobs?request_key={key}", {})["id"] == job["id"]
    jid = uuid.UUID(job["id"])
    build = archives.build

    def broken(*args, **kwargs):
        raise OSError("private storage path")

    monkeypatch.setattr(archives, "build", broken)
    assert archive_jobs.execute(jid, sessions=sessions) is False
    failed = client.get(path + "/archive-jobs").json()[0]
    assert failed["status"] == "failed" and "private" not in failed["error"]
    post_ok(client, path + f"/archive-jobs/{jid}/retry", {})
    monkeypatch.setattr(archives, "build", build)
    # Emulate a process that committed running then died before it published a result.
    with sessions() as db:
        db.get(EngineeringArchiveJob, jid).status = "running"
        db.commit()
    assert archive_jobs.execute(jid, sessions=sessions)
    assert not archive_jobs.execute(jid, sessions=sessions)
    rows = client.get(path + "/archive-jobs").json()
    assert rows[0]["status"] == "succeeded"
    target = path + f"/archive-jobs/{jid}/download"
    full = client.get(target)
    assert full.status_code == 200 and full.content.startswith(b"PK")
    ranged = client.get(target, headers={"Range": "bytes=0-31"})
    assert ranged.status_code == 206 and ranged.content == full.content[:32]
    other = post_ok(client, f"/api/v1/projects/{ids['project']}/works", {"title": "other"})
    unrelated = path.split("/works/")[0] + f"/works/{other['id']}/engineering"
    assert client.get(unrelated + f"/archive-jobs/{jid}/download").status_code == 404
    (archive_jobs.directory(jid) / "result.zip").unlink()
    assert client.get(path + "/archive-jobs").json()[0]["available"] is False
    post_ok(client, path + f"/archive-jobs/{jid}/retry", {})
    assert archive_jobs.execute(jid, sessions=sessions)
    assert client.get(path + "/archive-jobs").json()[0]["available"] is True


def test_task_ownership_cancel_and_retry(studio):
    client, sessions, _, path, _, _ = ready(studio)
    job = post_ok(client, path + f"/archive-jobs?request_key={uuid.uuid4()}", {})
    jid = uuid.UUID(job["id"])
    key = int.from_bytes(jid.bytes[:8], "big", signed=True)
    with sessions() as owner:
        owner.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        assert not archive_jobs.execute(jid, sessions=sessions)
    post_ok(client, path + f"/archive-jobs/{jid}/cancel", {})
    assert not archive_jobs.execute(jid, sessions=sessions)
    assert post_ok(client, path + f"/archive-jobs/{jid}/retry", {})["status"] == "pending"
