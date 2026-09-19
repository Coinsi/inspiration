"""Real media integration in disposable schemas; no production mutations."""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.models.identity import Membership, Project, User
from app.models.library import MediaVersion
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.library import service, worker
from tests.test_media_workflow import post_ok
from tests.test_media_workflow import studio as media_studio  # noqa: F401


@pytest.fixture
def library(media_studio, tmp_path, monkeypatch):  # noqa: F811 - imported pytest fixture
    client, sessions, ids = media_studio
    monkeypatch.setattr(settings, "library_staging_dir", str(tmp_path / "staging"))
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(service, "CHUNK_SIZE", 4096)
    source = tmp_path / "sample.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=24:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ]
    )
    return client, sessions, ids, source.read_bytes()


def upload(client, base, content, media_id=None):
    v = post_ok(
        client,
        f"{base}/uploads",
        {
            "filename": "sample.mp4",
            "size_bytes": len(content),
            "fingerprint": hashlib.sha256(content).hexdigest(),
            "media_id": media_id,
        },
    )
    for offset in range(0, len(content), v["chunk_size"]):
        r = client.post(
            f"{base}/versions/{v['id']}/chunk?offset={offset}",
            files={"file": ("chunk", content[offset : offset + v["chunk_size"]])},
        )
        assert r.status_code == 200, r.text
    post_ok(client, f"{base}/versions/{v['id']}/complete")
    worker.process(uuid.UUID(v["id"]))
    return client.get(f"{base}/versions/{v['id']}").json()


def test_upload_proxy_reference_version_pin_and_deletion(library, monkeypatch):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    v = upload(client, base, content)
    assert v["status"] == "ready", v
    assert 1900 <= v["duration_ms"] <= 2100 and v["width"] == 320
    assert v["original_hash"] == hashlib.sha256(content).hexdigest()
    with sessions() as db:
        proxy = db.get(Blob, v["proxy_hash"])
        assert "Audio:" in media_engine.run(["-i", proxy.storage_uri, "-f", "null", "-"])
    from app.storage import cas

    monkeypatch.setattr(cas, "read_bytes", lambda *_: pytest.fail("Whole file read is forbidden"))
    url = f"/api/v1/projects/{ids['project']}/blobs/{v['original_hash']}"
    r = client.get(url, headers={"Range": "bytes=20-39"})
    assert r.status_code == 206 and r.content == content[20:40]
    assert r.headers["content-length"] == "20"
    assert client.get(url, headers={"Range": "bytes=-12"}).content == content[-12:]
    assert client.get(url, headers={"Range": "bytes=999999999-"}).status_code == 416
    body = {
        "version_id": v["id"],
        "shot_id": ids["shot"],
        "start_ms": 100,
        "end_ms": 900,
        "purpose": "visual_reference",
    }
    usage = post_ok(client, f"{base}/usages", body)
    assert post_ok(client, f"{base}/usages", body)["id"] == usage["id"]
    assert client.post(f"{base}/usages", json={**body, "end_ms": 99999}).status_code == 422
    mid = v["media_id"]
    assert client.post(f"{base}/{mid}/trash").status_code == 409
    impact = client.get(f"{base}/{mid}/deletion-impact").json()
    assert not impact["can_trash"] and impact["references"][0]["version_id"] == v["id"]
    second = upload(client, base, content, mid)
    assert second["ordinal"] == 2 and second["id"] != v["id"]
    assert client.get(f"{base}/usages?shot_id={ids['shot']}").json()[0]["version_id"] == v["id"]
    assert len(client.get(base).json()[0]["versions"]) == 2
    assert client.delete(f"{base}/usages/{usage['id']}").status_code == 200
    assert post_ok(client, f"{base}/{mid}/trash")["physical_delete"] is False
    assert client.get(base).json() == []
    assert client.get(url).status_code == 404
    assert len(client.get(f"{base}?deleted=true").json()) == 1
    post_ok(client, f"{base}/{mid}/restore")
    assert client.get(url, headers={"Range": "bytes=0-3"}).content == content[:4]


def test_resume_offsets_cancel_and_permissions(library):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    v = post_ok(
        client,
        f"{base}/uploads",
        {"filename": "sample.mp4", "size_bytes": len(content), "fingerprint": "a" * 64},
    )
    path = f"{base}/versions/{v['id']}"
    assert client.post(f"{path}/complete").status_code == 409
    assert (
        client.post(f"{path}/chunk?offset=0", files={"file": ("chunk", content[:4096])}).status_code
        == 200
    )
    assert (
        client.post(f"{path}/chunk?offset=0", files={"file": ("chunk", content[:4096])}).status_code
        == 409
    )
    saved = client.get(path).json()
    assert saved["uploaded_bytes"] == 4096
    assert saved["chunk_hashes"] == [hashlib.sha256(content[:4096]).hexdigest()]
    assert client.post(f"{base}/{v['media_id']}/trash").status_code == 409
    with service.staging_path(v["id"]).open("ab") as stream:
        stream.write(b"uncommitted")
    assert (
        client.post(
            f"{path}/chunk?offset=4096", files={"file": ("chunk", content[4096:8192])}
        ).status_code
        == 200
    )
    assert service.staging_path(v["id"]).read_bytes() == content[:8192]
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    other = f"/api/v1/projects/{ids['other']}/library"
    assert client.get(f"{other}/versions/{v['id']}").status_code == 404
    assert client.post(f"{other}/{v['media_id']}/trash").status_code == 404
    post_ok(client, f"{path}/cancel")
    assert not service.staging_path(v["id"]).exists()
    assert (
        client.post(f"{path}/chunk?offset=8192", files={"file": ("chunk", b"x")}).status_code == 409
    )
    with sessions() as db:
        m = db.scalar(select(Membership).where(Membership.project_id == uuid.UUID(ids["project"])))
        m.role = "viewer"
        db.commit()
    assert (
        client.post(
            f"{base}/uploads", json={"filename": "x.mp4", "size_bytes": 2, "fingerprint": "a" * 64}
        ).status_code
        == 403
    )
    assert client.post(f"{base}/{v['media_id']}/trash").status_code == 403


def test_failed_decode_and_crashed_processing_can_recover(library):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    bad = upload(client, base, b"not a video")
    assert bad["status"] == "failed" and bad["proxy_hash"] is None
    assert (
        client.post(
            f"{base}/usages",
            json={"version_id": bad["id"], "shot_id": ids["shot"], "start_ms": 0, "end_ms": 100},
        ).status_code
        == 409
    )
    with sessions() as db:
        v = db.get(MediaVersion, uuid.UUID(bad["id"]))
        v.status, v.size_bytes, v.uploaded_bytes = "processing", len(content), len(content)
        service.staging_path(v.id).write_bytes(content)
        db.commit()
    worker.process(uuid.UUID(bad["id"]))
    assert client.get(f"{base}/versions/{bad['id']}").json()["status"] == "ready"


def test_reference_and_trash_serialize_in_both_orders(library):
    from app.modules.library.schemas import UsageIn

    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    v = upload(client, base, content)
    data = {"version_id": v["id"], "shot_id": ids["shot"], "start_ms": 0, "end_ms": 1000}
    with sessions() as db, ThreadPoolExecutor(max_workers=1) as pool:
        ctx = SimpleNamespace(
            project=db.get(Project, uuid.UUID(ids["project"])), user=db.get(User, ids["user"])
        )
        usage = service.create_usage(db, ctx, UsageIn(**data))
        pending = pool.submit(client.post, f"{base}/{v['media_id']}/trash")
        try:
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.3)
        finally:
            db.commit()
        assert pending.result(timeout=5).status_code == 409
        assert client.delete(f"{base}/usages/{usage['id']}").status_code == 200
        service.trash(db, ctx, uuid.UUID(v["media_id"]))
        db.flush()
        pending = pool.submit(client.post, f"{base}/usages", json=data)
        try:
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.3)
        finally:
            db.commit()
        assert pending.result(timeout=5).status_code == 404
        assert client.get(f"{base}/usages").json() == []


def test_worker_exclusion_and_interrupted_job_requeue(library, monkeypatch):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    v = upload(client, base, content)
    version_id = uuid.UUID(v["id"])
    with sessions() as db:
        engine = db.get_bind()
        item = db.get(MediaVersion, version_id)
        item.status = "processing"
        db.commit()
    monkeypatch.setattr(worker, "engine", engine)
    key = uuid.uuid4().int % (2**62)
    monkeypatch.setattr(worker, "QUEUE_LOCK", key)
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        try:
            assert worker.run_once() is False
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            conn.commit()
    assert worker.run_once(canceled=lambda: True) is True
    assert client.get(f"{base}/versions/{version_id}").json()["status"] == "queued"
    assert worker.run_once() is True
    assert client.get(f"{base}/versions/{version_id}").json()["status"] == "ready"
    assert worker.run_once() is False
