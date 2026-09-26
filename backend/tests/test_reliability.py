"""Regression coverage for file-backed exports, auth controls and readiness."""

# ruff: noqa: F811
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import health
from app.core.config import settings
from app.core.security import create_access_token
from app.modules.generation import media_engine
from app.storage import cas
from tests.test_media_workflow import studio  # noqa: F401


def test_remote_download_is_bounded_and_cancelable(monkeypatch):
    reads = []

    class Stream:
        left = 20

        def read(self, size):
            assert 0 < size <= 1024 * 1024
            reads.append(size)
            if self.left == 0:
                return b""
            self.left -= 1
            return b"x" * size

    @contextmanager
    def opened(*args):
        yield Stream()

    monkeypatch.setattr(cas, "open_range", opened)
    hasher = hashlib.sha256()
    for _ in range(20):
        hasher.update(b"x" * 1024 * 1024)
    blob = SimpleNamespace(
        storage_uri="bucket/key", size_bytes=20 * 1024 * 1024, hash=hasher.hexdigest()
    )
    with cas.local_file(blob) as path:
        assert path.stat().st_size == blob.size_bytes
        parent = path.parent
    assert not parent.exists()
    assert len(reads) == 21
    with pytest.raises(media_engine.Canceled):
        with cas.local_file(blob, lambda: True):
            pytest.fail("Canceled source should never be delivered")
    blob.size_bytes += 1
    with pytest.raises(ValueError, match="不完整"):
        with cas.local_file(blob):
            pass


def test_render_file_never_reads_whole_files(tmp_path, monkeypatch):
    source = tmp_path / "input.png"
    source.write_bytes(media_engine.mock_image())
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("Whole-file read"))
    clips = [{"blob_hash": "image", "output_type": "image", "duration_ms": 500}]
    with media_engine.render_file(clips, lambda _: source, {"height": 360}) as output:
        assert output.stat().st_size > 0
        with output.open("rb") as stream:
            assert stream.read(8)[4:8] == b"ftyp"
        parent = output.parent
    assert not parent.exists()
    assert source.exists()


def test_logout_revokes_only_current_token(studio):
    client, _, ids = studio
    old = client.headers["Authorization"]
    other = create_access_token(str(ids["user"]))
    assert client.get("/api/v1/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/me").status_code == 401
    media = f"/api/v1/projects/{ids['project']}/blobs/missing"
    assert client.get(media, params={"token": old.split()[1]}).status_code == 401
    client.headers["Authorization"] = old + "="
    assert client.get("/api/v1/me").status_code == 401
    assert client.get(media, params={"token": old.split()[1] + "="}).status_code == 401
    client.headers["Authorization"] = f"Bearer {other}"
    assert client.get("/api/v1/me").status_code == 200
    assert old != f"Bearer {other}"


def test_failed_logins_are_counted_across_requests(studio):
    client, _, _ = studio
    for _ in range(30):
        response = client.post(
            "/api/v1/auth/login", json={"username": "missing", "password": "bad"}
        )
        assert response.status_code == 401
    response = client.post("/api/v1/auth/login", json={"username": "missing", "password": "bad"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_readiness_distinguishes_optional_services(monkeypatch):
    monkeypatch.setattr(settings, "celery_eager", True)
    monkeypatch.setattr(settings, "transcriber_url", "")
    monkeypatch.setattr(settings, "library_indexer_url", "")
    monkeypatch.setattr(health, "database_ready", lambda: True)
    monkeypatch.setattr(health, "storage_ready", lambda: True)
    assert health.readiness()["status"] == "ok"
    assert health.readiness()["optional"]["transcriber"] == "not_configured"
    monkeypatch.setattr(health, "database_ready", lambda: False)
    assert health.readiness()["status"] == "unavailable"


def test_render_job_uses_streaming_storage(studio, monkeypatch):
    from app.models.generation import GenerationJob
    from app.modules.generation import jobs

    _, sessions, ids = studio
    with sessions() as db:
        blob = cas.put_bytes(db, media_engine.mock_image(), "image/png")
        job = GenerationJob(
            project_id=ids["project"],
            target_type="shot",
            target_id=ids["shot"],
            provider="local",
            request_type="video",
            status="pending",
            estimated_cost=0,
            created_by=ids["user"],
            params={},
            input_snapshot={
                "operation": "render",
                "clips": [{"blob_hash": blob.hash, "output_type": "image", "duration_ms": 500}],
                "options": {"height": 360},
            },
        )
        db.add(job)
        db.commit()
        monkeypatch.setattr(cas, "read_bytes", lambda _: pytest.fail("Whole source read"))
        monkeypatch.setattr(cas, "put_bytes", lambda *a, **k: pytest.fail("Whole output write"))
        jobs.execute(db, job.id)
        db.refresh(job)
        assert job.status == "succeeded", job.error


def test_backup_verification_and_restoring_objects(studio, tmp_path, monkeypatch):
    from app import maintenance
    from app.models.storage import Blob

    _, sessions, _ = studio
    monkeypatch.setattr(maintenance, "SessionLocal", sessions)
    with sessions() as db:
        blob = cas.put_bytes(db, media_engine.mock_image(), "image/png")
        key = blob.hash
        db.commit()
    backup = tmp_path / "backup"
    target = backup / "objects" / key[:2] / key
    target.parent.mkdir(parents=True)
    target.write_bytes(media_engine.mock_image())
    dump = backup / "database.dump"
    dump.write_bytes(b"test fixture; actual pg_restore is checked separately")
    manifest = {
        "format": 1,
        "media_included": True,
        "files": [
            {
                "path": p.relative_to(backup).as_posix(),
                "sha256": maintenance.digest(p),
                "bytes": p.stat().st_size,
            }
            for p in (dump, target)
        ],
    }
    (backup / "manifest.json").write_text(json.dumps(manifest))
    assert maintenance.verify(backup)["status"] == "verified"
    monkeypatch.setattr(settings, "fs_storage_dir", str(tmp_path / "restored"))
    assert maintenance.restore_objects(backup)["objects"] == 1
    with sessions() as db:
        blob = db.get(Blob, key)
        assert Path(blob.storage_uri).is_relative_to(tmp_path / "restored")
        assert cas.read_bytes(blob) == target.read_bytes()
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="校验失败"):
        maintenance.verify(backup)


def test_readiness_endpoint_returns_503(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(
        health,
        "readiness",
        lambda: {"status": "unavailable", "checks": {"database": "unavailable"}},
    )
    assert TestClient(app).get("/health/ready").status_code == 503
