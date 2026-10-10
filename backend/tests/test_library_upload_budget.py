"""Real DB admission/offset checks; never allocate large test files."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from sqlalchemy import select

from app.core.config import settings
from app.models.library import MediaVersion
from app.modules.library import service, upload_budget
from tests.test_library import library  # noqa: F401
from tests.test_media_workflow import post_ok
from tests.test_media_workflow import studio as media_studio  # noqa: F401


def request(size=6000):
    return {"filename": "budget.mp4", "size_bytes": size, "fingerprint": "c" * 64}


def test_budget_rejects_new_upload_without_leaving_media_and_releases_on_cancel(
    library, monkeypatch
):
    client, sessions, ids, _ = library
    base = f"/api/v1/projects/{ids['project']}/library"
    monkeypatch.setattr(settings, "library_staging_max_bytes", 10000)
    first = post_ok(client, f"{base}/uploads", request())
    rejected = client.post(f"{base}/uploads", json=request())
    assert rejected.status_code == 409
    assert "容量" in rejected.text
    assert len(client.get(base).json()) == 1
    post_ok(client, f"{base}/versions/{first['id']}/cancel")
    assert post_ok(client, f"{base}/uploads", request())["status"] == "uploading"


def test_failed_upload_retains_reservation_for_retry(library, monkeypatch):
    client, sessions, ids, _ = library
    base = f"/api/v1/projects/{ids['project']}/library"
    monkeypatch.setattr(settings, "library_staging_max_bytes", 10000)
    first = post_ok(client, f"{base}/uploads", request())
    with sessions() as db:
        db.get(MediaVersion, uuid.UUID(first["id"])).status = "failed"
        db.commit()
    assert client.post(f"{base}/uploads", json=request()).status_code == 409


def test_disk_shortage_preserves_uploaded_offset_and_can_resume(library, monkeypatch):
    client, sessions, ids, _ = library
    base = f"/api/v1/projects/{ids['project']}/library"
    monkeypatch.setattr(settings, "library_staging_reserve_bytes", 1000)
    space = SimpleNamespace(free=100000)
    monkeypatch.setattr(upload_budget.shutil, "disk_usage", lambda _: space)
    first = post_ok(client, f"{base}/uploads", request(8192))
    url = f"{base}/versions/{first['id']}/chunk"
    chunk = b"x" * 4096
    assert client.post(url + "?offset=0", files={"file": ("chunk", chunk)}).status_code == 200
    space.free = 4096
    assert client.post(url + "?offset=4096", files={"file": ("chunk", chunk)}).status_code == 409
    actual = client.get(f"{base}/versions/{first['id']}").json()
    assert actual["uploaded_bytes"] == 4096 and actual["status"] == "uploading"
    assert service.staging_path(first["id"]).stat().st_size == 4096
    space.free = 100000
    assert client.post(url + "?offset=4096", files={"file": ("chunk", chunk)}).status_code == 200
    with sessions() as db:
        assert db.scalar(select(MediaVersion.uploaded_bytes)) == 8192


def test_admission_reserves_unreceived_bytes_against_free_disk(library, monkeypatch):
    client, _, ids, _ = library
    base = f"/api/v1/projects/{ids['project']}/library"
    monkeypatch.setattr(settings, "library_staging_reserve_bytes", 1000)
    monkeypatch.setattr(upload_budget.shutil, "disk_usage", lambda _: SimpleNamespace(free=10000))
    post_ok(client, f"{base}/uploads", request())
    assert client.post(f"{base}/uploads", json=request()).status_code == 409


def test_simultaneous_uploads_cannot_overbook_the_staging_budget(library, monkeypatch):
    client, _, ids, _ = library
    base = f"/api/v1/projects/{ids['project']}/library"
    monkeypatch.setattr(settings, "library_staging_max_bytes", 10000)
    start = Barrier(2)

    def submit():
        start.wait(timeout=5)
        return client.post(f"{base}/uploads", json=request()).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(submit) for _ in range(2)]
        assert sorted(result.result(timeout=10) for result in results) == [200, 409]
    assert len(client.get(base).json()) == 1
