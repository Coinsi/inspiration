"""Real PostgreSQL reservations and private staging cleanup, never production files."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.errors import Conflict
from app.models.engineering import EngineeringArchiveJob
from app.modules.engineering import archive_capacity, archive_cleanup, archive_jobs
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_parallel_reservations_cannot_spend_same_capacity(studio, monkeypatch):
    _, sessions, ids, path, _, _ = ready(studio)
    work_id = uuid.UUID(path.split("/works/")[1].split("/")[0])
    keys = []
    with sessions() as db:
        for _ in range(2):
            row = EngineeringArchiveJob(
                work_id=work_id,
                created_by=ids["user"],
                request_key=uuid.uuid4(),
                revision=0,
                snapshot={},
            )
            db.add(row)
            db.flush()
            keys.append(row.id)
        db.commit()
    monkeypatch.setattr(settings, "archive_staging_quota_bytes", 60)
    monkeypatch.setattr(settings, "archive_project_quota_bytes", 60)

    def reserve(key):
        with sessions() as db:
            try:
                archive_capacity.reserve(db, db.get(EngineeringArchiveJob, key), 40)
                db.commit()
                return True
            except Conflict:
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, keys)) == [False, True]
    with sessions() as db:
        assert db.scalar(select(func.sum(EngineeringArchiveJob.reserved_bytes))) == 40


def test_expiration_defers_readers_and_releases_only_private_files(studio):
    client, sessions, _, path, _, _ = ready(studio)
    job = post_ok(
        client, path + f"/archive-uploads?request_key={uuid.uuid4()}&size=6&filename=x.zip", {}
    )
    jid = uuid.UUID(job["id"])
    assert client.put(path + f"/archive-uploads/{jid}?offset=0", content=b"abc").status_code == 200
    now = datetime.now(UTC) + timedelta(days=8)
    key = int.from_bytes(jid.bytes[:8], "big", signed=True)
    with sessions() as reader:
        reader.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": key})
        assert not archive_cleanup.cleanup(sessions, jid, now=now)
    assert archive_cleanup.cleanup(sessions, jid, now=now)
    assert not archive_jobs.directory(jid).exists()
    with sessions() as db:
        row = db.get(EngineeringArchiveJob, jid)
        assert row.status == "expired" and row.reserved_bytes == 0
    renewed = post_ok(client, path + f"/archive-jobs/{jid}/retry", {})
    assert renewed["status"] == "uploading" and renewed["uploaded_bytes"] == 0


def test_cleanup_refuses_unknown_files_and_retries_after_interruption(studio, monkeypatch):
    client, sessions, _, path, _, _ = ready(studio)
    job = post_ok(
        client, path + f"/archive-uploads?request_key={uuid.uuid4()}&size=6&filename=x.zip", {}
    )
    jid = uuid.UUID(job["id"])
    folder = archive_jobs.directory(jid)
    folder.mkdir(parents=True)
    (folder / "upload.zip").write_bytes(b"abc")
    unknown = folder / "keep.txt"
    unknown.write_text("keep")
    now = datetime.now(UTC) + timedelta(days=8)
    with pytest.raises(ValueError, match="Unknown"):
        archive_cleanup.cleanup(sessions, jid, now=now)
    assert (folder / "upload.zip").exists() and unknown.exists()
    unknown.unlink()
    remove = archive_cleanup.remove_files

    def interrupted(key):
        remove(key)
        raise OSError("worker interrupted")

    monkeypatch.setattr(archive_cleanup, "remove_files", interrupted)
    with pytest.raises(OSError):
        archive_cleanup.cleanup(sessions, jid, now=now)
    with sessions() as db:
        row = db.get(EngineeringArchiveJob, jid)
        assert row.status == "purging" and row.reserved_bytes > 0
    monkeypatch.setattr(archive_cleanup, "remove_files", remove)
    assert archive_cleanup.cleanup(sessions, jid, now=now)


@pytest.mark.parametrize("limit", ["project", "disk", "global"])
def test_capacity_rejection_rolls_back_new_jobs_and_legacy_export(studio, monkeypatch, limit):
    from types import SimpleNamespace

    client, sessions, _, path, _, _ = ready(studio)
    if limit == "disk":
        monkeypatch.setattr(
            archive_capacity.shutil, "disk_usage", lambda _: SimpleNamespace(free=0)
        )
    else:
        name = (
            "archive_project_quota_bytes" if limit == "project" else "archive_staging_quota_bytes"
        )
        monkeypatch.setattr(settings, name, 1)
    response = client.post(
        path + f"/archive-uploads?request_key={uuid.uuid4()}&size=6&filename=x.zip", json={}
    )
    assert response.status_code == 409, response.text
    response = client.get(path + "/archive")
    assert response.status_code == 409, response.text
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(EngineeringArchiveJob)) == 0
