"""Real worker/CAS persistence and recovery in disposable PostgreSQL schemas."""

# ruff: noqa: F811
import hashlib

import pytest
from PIL import Image

from app.core.config import settings
from app.models.library import LibraryMedia, MediaVersion
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.library import media_ingest, service, worker
from app.storage import cas
from tests.test_media_workflow import studio as media_studio  # noqa: F401


@pytest.fixture
def ingestion(media_studio, tmp_path, monkeypatch):
    _, sessions, ids = media_studio
    monkeypatch.setattr(settings, "library_staging_dir", str(tmp_path / "staging"))
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(
        cas, "read_bytes", lambda *_: pytest.fail("Whole source reads are forbidden")
    )
    return sessions, ids, tmp_path


def source_file(folder, kind):
    path = folder / {"image": "source.png", "audio": "source.wav", "video": "source.mp4"}[kind]
    if kind == "image":
        Image.new("RGB", (80, 48), "purple").save(path)
    elif kind == "audio":
        media_engine.run(["-f", "lavfi", "-i", "sine=duration=0.7", str(path)])
    else:
        media_engine.run(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=80x48:r=10:d=0.7",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ]
        )
    return path


def queued(ingestion, path, kind):
    sessions, ids, _ = ingestion
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    with sessions() as db:
        media = LibraryMedia(project_id=ids["project"], name=path.name)
        db.add(media)
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            media_type=kind,
            ordinal=1,
            filename=path.name,
            size_bytes=len(data),
            uploaded_bytes=len(data),
            fingerprint=digest,
            status="queued",
        )
        db.add(version)
        db.commit()
        identity = version.id
    service.staging_path(identity).write_bytes(data)
    return identity, digest


@pytest.mark.parametrize("kind", ["image", "audio", "video"])
def test_worker_imports_each_type_with_safe_preview_and_immutable_original(ingestion, kind):
    sessions, _, folder = ingestion
    source = source_file(folder, kind)
    run, digest = queued(ingestion, source, kind)
    worker.process(run)
    with sessions() as db:
        version = db.get(MediaVersion, run)
        assert version.status == "ready", version.error
        assert version.media_type == kind and version.original_hash == digest
        original = db.get(Blob, digest)
        with cas.open_range(original) as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == digest
        assert (
            original.mime
            == {"image": "image/png", "audio": "audio/wav", "video": "video/mp4"}[kind]
        )
        proxy = db.get(Blob, version.proxy_hash)
        with cas.local_file(proxy) as preview:
            actual = media_ingest.probe_media(preview, kind)
        if kind == "image":
            assert version.duration_ms is None and (version.width, version.height) == (80, 48)
            assert version.proxy_hash == version.poster_hash
            assert proxy.mime == "image/jpeg" and actual["duration_ms"] is None
        elif kind == "audio":
            assert version.width is None and version.height is None and version.poster_hash is None
            assert 600 <= version.duration_ms <= 850 and proxy.mime == "audio/mp4"
            assert actual["has_audio"] and not actual["has_video"]
        else:
            assert version.poster_hash and proxy.mime == "video/mp4"
            assert actual["has_video"] and (version.width, version.height) == (80, 48)
    assert not service.staging_path(run).exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("kind", ["image", "audio"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_preview_failure_or_shutdown_preserves_original_and_recovers_from_cas(
    ingestion, monkeypatch, kind, interrupted
):
    sessions, _, folder = ingestion
    run, digest = queued(ingestion, source_file(folder, kind), kind)
    real_preview = worker.build_preview

    def fail(*_args):
        if interrupted:
            raise media_engine.Canceled()
        raise ValueError("deliberate temporary processing failure")

    monkeypatch.setattr(worker, "build_preview", fail)
    worker.process(run)
    with sessions() as db:
        version = db.get(MediaVersion, run)
        assert version.status == ("queued" if interrupted else "failed")
        assert version.original_hash == digest and version.proxy_hash is None
        assert version.error is None if interrupted else version.error is not None
        version.status = "queued"
        db.commit()
    service.staging_path(run).unlink()
    monkeypatch.setattr(worker, "build_preview", real_preview)
    worker.process(run)
    with sessions() as db:
        version = db.get(MediaVersion, run)
        assert version.status == "ready" and version.original_hash == digest
        assert version.proxy_hash is not None


def test_retry_rejects_a_same_size_changed_original(ingestion, monkeypatch):
    sessions, _, folder = ingestion
    run, digest = queued(ingestion, source_file(folder, "audio"), "audio")
    monkeypatch.setattr(
        worker, "build_preview", lambda *_: (_ for _ in ()).throw(ValueError("fail"))
    )
    worker.process(run)
    staged = service.staging_path(run)
    changed = bytearray(staged.read_bytes())
    changed[-1] ^= 1
    staged.write_bytes(changed)
    with sessions() as db:
        db.get(MediaVersion, run).status = "queued"
        db.commit()
    worker.process(run)
    with sessions() as db:
        version = db.get(MediaVersion, run)
        assert version.status == "failed" and version.original_hash == digest
        assert version.proxy_hash is None


@pytest.mark.parametrize(
    "actual,declared", [("image", "video"), ("video", "audio"), ("audio", "image")]
)
def test_declared_type_cannot_disguise_another_media_class(ingestion, actual, declared):
    sessions, _, folder = ingestion
    run, _ = queued(ingestion, source_file(folder, actual), declared)
    worker.process(run)
    with sessions() as db:
        version = db.get(MediaVersion, run)
        assert version.status == "failed"
        assert version.original_hash is None and version.proxy_hash is None
    assert service.staging_path(run).exists()
