"""Real original-range extraction and source retention during background work."""

# ruff: noqa: F811
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import settings
from app.models.generation import Generation
from app.models.library import LibraryMedia, MediaVersion
from app.models.shot import Shot
from app.modules.generation import jobs
from app.modules.library import materialization
from app.storage import cas
from tests.test_media_workflow import changing_video, post_ok, studio  # noqa: F401


def seed(sessions, ids, data):
    with sessions() as db:
        blob = cas.put_bytes(db, data, "video/mp4")
        media = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="Original range")
        db.add(media)
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            ordinal=1,
            filename="original.mp4",
            fingerprint="a" * 64,
            size_bytes=len(data),
            uploaded_bytes=len(data),
            status="ready",
            duration_ms=2000,
            original_hash=blob.hash,
            proxy_hash=blob.hash,
        )
        db.add(version)
        db.commit()
        return str(media.id), str(version.id), blob.hash


def test_real_derivatives_idempotence_and_permissions(studio, changing_video, monkeypatch):
    client, sessions, ids = studio
    media, version, source = seed(sessions, ids, changing_video)
    root = f"/api/v1/projects/{ids['project']}"
    usage = post_ok(
        client,
        root + "/library/usages",
        {"version_id": version, "shot_id": ids["shot"], "start_ms": 1200, "end_ms": 1800},
    )
    endpoint = root + f"/library/usages/{usage['id']}/materialize"
    # The large original must never use the whole-file bytes API.
    original_reader = cas.read_bytes
    monkeypatch.setattr(
        cas,
        "read_bytes",
        lambda *_: (_ for _ in ()).throw(AssertionError("Unbounded original read")),
    )
    outputs = {}
    for kind in ["image", "video", "audio"]:
        request = {"request_key": str(uuid.uuid4()), "output_type": kind}
        job = post_ok(client, endpoint, request)
        assert job["status"] == "succeeded", job
        assert post_ok(client, endpoint, request)["id"] == job["id"]
        assert (
            client.post(
                endpoint, json={**request, "output_type": "audio" if kind != "audio" else "video"}
            ).status_code
            == 409
        )
        with sessions() as db:
            g = db.scalar(select(Generation).where(Generation.job_id == uuid.UUID(job["id"])))
            assert g.output_type == kind
            outputs[kind] = str(g.id)
            assert g.input_refs["library_origin"]["version_id"] == version
            assert g.input_refs["library_origin"]["start_ms"] == 1200
            assert db.get(Shot, uuid.UUID(ids["shot"])).selected_generation_id is None
            if kind == "image":
                from app.models.storage import Blob

                with cas.open_range(db.get(Blob, g.output_blob_hash)) as stream:
                    image = Image.open(io.BytesIO(stream.read()))
                    r, green, b = image.getpixel((160, 90))
                    assert b > 200 and r < 20  # actual original blue frame, not an arbitrary poster
            else:
                actual = g.input_refs["actual_media"]
                assert 500 <= actual["duration_ms"] <= 750
                assert actual["has_audio"]
    monkeypatch.setattr(cas, "read_bytes", original_reader)
    timeline = post_ok(client, root + "/timelines", {"name": "素材衔接"})
    sources = client.get(root + "/audio-sources").json()["items"]
    assert next(s for s in sources if s["id"] == outputs["audio"])["duration_ms"] >= 600
    document = {
        "revision": 0,
        "items": [{"shot_id": ids["shot"], "generation_id": outputs["video"], "duration_ms": 600}],
        "audio": [{"generation_id": outputs["audio"], "duration_ms": 600, "gain_db": -12}],
        "subtitles": [{"start_ms": 0, "end_ms": 600, "text": "原片衔接"}],
    }
    response = client.put(root + f"/timelines/{timeline['id']}/document", json=document)
    assert response.status_code == 200, response.text
    rendered = post_ok(
        client, root + f"/timelines/{timeline['id']}/render", {"revision": 1, "height": 720}
    )
    assert rendered["status"] == "succeeded", rendered
    # Provider contract test: a real extracted frame becomes encoded model input.
    from app.adapters.generation.mock import MockProvider
    from app.modules.generation import service as generation_service

    class ReferenceProvider(MockProvider):
        def capabilities(self):
            caps = super().capabilities()
            caps.features = {"img2img"}
            caps.max_reference_images = 1
            return caps

        def submit(self, request):
            assert len(request.references) == 1
            assert request.references[0].data_url.startswith("data:image/png;base64,")
            return super().submit(request)

    monkeypatch.setattr(
        generation_service, "_provider_instance", lambda *_, **__: ReferenceProvider()
    )
    generated = post_ok(
        client,
        root + f"/shots/{ids['shot']}/generate",
        {
            "source_generation_id": outputs["image"],
            "prompt_override": "使用原片参考帧",
            "use_references": False,
        },
    )
    assert generated["status"] == "succeeded", generated
    assert (
        generated["input_snapshot"]["reference_sources"][0]["library_origin"]["version_id"]
        == version
    )
    assert (
        client.post(
            endpoint,
            json={"request_key": str(uuid.uuid4()), "output_type": "image", "time_ms": 1900},
        ).status_code
        == 422
    )
    with sessions() as db:
        db.get(Shot, uuid.UUID(ids["shot"])).status = "locked"
        db.commit()
    assert (
        client.post(
            endpoint, json={"request_key": str(uuid.uuid4()), "output_type": "image"}
        ).status_code
        == 409
    )
    assert client.post(
        endpoint.replace(ids["project"], str(ids["other"])),
        json={"request_key": str(uuid.uuid4()), "output_type": "image"},
    ).status_code in (403, 404)


def test_unlinked_inflight_job_retains_original_and_retry_checks_trash(
    studio, changing_video, monkeypatch
):
    client, sessions, ids = studio
    media, version, source = seed(sessions, ids, changing_video)
    root = f"/api/v1/projects/{ids['project']}"
    usage = post_ok(
        client,
        root + "/library/usages",
        {"version_id": version, "shot_id": ids["shot"], "start_ms": 0, "end_ms": 1000},
    )
    monkeypatch.setattr(settings, "celery_eager", False)
    monkeypatch.setattr(settings, "generation_executor", "local")
    monkeypatch.setattr(jobs.pool, "submit", lambda *_: None)
    job = post_ok(
        client,
        root + f"/library/usages/{usage['id']}/materialize",
        {"request_key": str(uuid.uuid4()), "output_type": "video"},
    )
    assert job["status"] == "pending"
    assert client.delete(root + f"/library/usages/{usage['id']}").status_code == 200
    impact = client.get(root + f"/library/{media}/deletion-impact").json()
    assert not impact["can_trash"] and job["id"] in impact["active_versions"]
    assert client.post(root + f"/library/{media}/trash").status_code == 409
    post_ok(client, root + f"/jobs/{job['id']}/cancel")
    post_ok(client, root + f"/library/{media}/trash")
    assert client.post(root + f"/jobs/{job['id']}/retry").status_code == 404
    post_ok(client, root + f"/library/{media}/restore")
    retried = post_ok(client, root + f"/jobs/{job['id']}/retry")
    with sessions() as db:
        jobs.execute(db, uuid.UUID(retried["id"]))
    assert client.get(root + f"/jobs/{retried['id']}").json()["status"] == "succeeded"


@pytest.mark.parametrize("exception_type", [ValueError, OSError])
def test_materialization_job_hides_decoder_and_filesystem_details(
    studio, changing_video, monkeypatch, caplog, exception_type
):
    client, sessions, ids = studio
    _, version, _ = seed(sessions, ids, changing_video)
    root = f"/api/v1/projects/{ids['project']}"
    usage = post_ok(
        client,
        root + "/library/usages",
        {"version_id": version, "shot_id": ids["shot"], "start_ms": 0, "end_ms": 1000},
    )
    private_detail = r"ffmpeg C:\private\media\source.mp4 failed; api_key=private-test-key"

    def broken_decoder(*args, **kwargs):
        raise exception_type(private_detail)

    monkeypatch.setattr(materialization.media_engine, "run", broken_decoder)
    # Go through the real local generation worker: its persisted error and event
    # messages are public, not just the exception returned by this module.
    job = post_ok(
        client,
        root + f"/library/usages/{usage['id']}/materialize",
        {"request_key": str(uuid.uuid4()), "output_type": "audio"},
    )
    assert job["status"] == "failed"
    persisted = client.get(root + f"/jobs/{job['id']}").json()
    assert persisted["error"] == (
        "素材提取失败，请检查原素材是否可读、剩余磁盘空间及存储服务后重试。"
    )
    assert "private-test-key" not in str(persisted)
    assert "source.mp4" not in str(persisted)
    assert private_detail in caplog.text
    with sessions() as db:
        assert not db.scalar(select(Generation).where(Generation.job_id == uuid.UUID(job["id"])))


def test_materialization_preserves_safe_validation_in_job_error(studio, monkeypatch):
    client, sessions, ids = studio
    _, version, _ = seed(sessions, ids, b"source for explicit missing audio validation")
    root = f"/api/v1/projects/{ids['project']}"
    usage = post_ok(
        client,
        root + "/library/usages",
        {"version_id": version, "shot_id": ids["shot"], "start_ms": 0, "end_ms": 1000},
    )
    monkeypatch.setattr(
        materialization,
        "probe_media",
        lambda *args: {"has_video": True, "has_audio": False, "duration_ms": 2000},
    )
    job = post_ok(
        client,
        root + f"/library/usages/{usage['id']}/materialize",
        {"request_key": str(uuid.uuid4()), "output_type": "audio"},
    )
    assert job["status"] == "failed"
    assert job["error"] == "这个素材没有音轨，无法提取音频"
    empty_db = SimpleNamespace(get=lambda *_: None)
    with pytest.raises(materialization.MaterializationValidationError, match="原素材文件不存在"):
        materialization.process(empty_db, {"source_blob": "missing"}, lambda: False)


def test_materialization_cancellation_keeps_type_and_closes_stream_and_temp(
    tmp_path, monkeypatch, caplog
):
    from app.modules.generation import media_engine

    stream = io.BytesIO(b"object storage upload")
    blob = SimpleNamespace(storage_uri=str(tmp_path / "not-local"))
    db = SimpleNamespace(get=lambda *_: blob)
    temporary_directory = materialization.tempfile.TemporaryDirectory
    created = []

    def tracked_directory(**kwargs):
        directory = temporary_directory(dir=tmp_path, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(materialization.tempfile, "TemporaryDirectory", tracked_directory)
    monkeypatch.setattr(materialization.cas, "open_range", lambda *_: stream)
    snapshot = {
        "source_blob": "object-hash",
        "library_origin": {"media_type": "audio", "start_ms": 0, "end_ms": 1000},
        "options": {"output_type": "audio", "time_ms": None},
    }
    with pytest.raises(media_engine.Canceled):
        materialization.process(db, snapshot, lambda: True)
    assert stream.closed
    assert created and all(not path.exists() for path in created)
    assert "Library materialization failed" not in caplog.text
