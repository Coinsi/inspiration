"""Uploaded references work without semantic indexing, through catalog and execution."""

# ruff: noqa: F811
import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.adapters.contracts import GenerationResult, Output
from app.adapters.generation.mock import MockProvider
from app.adapters.generation.video_v1 import VideoV1Provider
from app.core.config import settings
from app.models.generation import GenerationJob
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex
from app.modules.generation import jobs, media_engine
from app.modules.library import worker
from tests.test_media_workflow import changing_video, post_ok, studio  # noqa: F401
from tests.test_workbench import body


@pytest.fixture
def uploaded(studio, tmp_path, monkeypatch):
    client, sessions, ids = studio
    monkeypatch.setattr(settings, "library_staging_dir", str(tmp_path / "staging"))
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    root = f"/api/v1/projects/{ids['project']}"

    def put(content, filename="参考图.png", media_id=None):
        base = root + "/library"
        version = post_ok(
            client,
            base + "/uploads",
            {
                "filename": filename,
                "size_bytes": len(content),
                "fingerprint": hashlib.sha256(content).hexdigest(),
                "media_id": media_id,
            },
        )
        path = base + f"/versions/{version['id']}"
        result = client.post(path + "/chunk?offset=0", files={"file": (filename, content)})
        assert result.status_code == 200, result.text
        post_ok(client, path + "/complete")
        worker.process(uuid.UUID(version["id"]))
        ready = client.get(path).json()
        assert ready["status"] == "ready", ready
        return ready

    return client, sessions, ids, root, put


def reference_caps(monkeypatch):
    original = MockProvider.capabilities

    def caps(self):
        result = original(self)
        result.features.update({"reference", "first_frame", "last_frame", "video_reference"})
        result.max_reference_images, result.max_reference_videos = 8, 2
        return result

    monkeypatch.setattr(MockProvider, "capabilities", caps)


def test_image_without_index_reaches_provider_and_fixed_version_survives_new_upload(
    uploaded,
    monkeypatch,
):
    client, sessions, ids, root, put = uploaded
    original = media_engine.mock_image()
    version = put(original)
    source = {"media_version_id": version["id"], "role": "reference"}
    reference_caps(monkeypatch)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(MediaIndex)) == 0
    listing = client.get(root + "/workbench/references").json()
    assert listing["has_more"] is False and len(listing["items"]) == 1
    item = listing["items"][0]
    assert item["source_type"] == "media_version" and item["id"] == version["id"]
    assert client.get(root + "/blobs/" + item["blob_hash"]).content == original
    # A missing, pending or failed index never changes manual selection.
    for status in ("queued", "running", "failed"):
        with sessions() as db:
            index = db.scalar(select(MediaIndex))
            if not index:
                index = MediaIndex(version_id=uuid.UUID(version["id"]), model_key="unavailable")
                db.add(index)
            index.status = status
            db.commit()
        assert client.get(root + "/workbench/references").json()["items"] == [item]
    # Pending replacement doesn't make the old source disappear.
    pending = post_ok(
        client,
        root + "/library/uploads",
        {
            "filename": "新版.png",
            "size_bytes": 123,
            "fingerprint": "a" * 64,
            "media_id": version["media_id"],
        },
    )
    assert client.get(root + "/workbench/references").json()["items"] == [item]
    post_ok(client, root + f"/library/versions/{pending['id']}/cancel")
    newer = put(
        media_engine.transform_image(original, {"rotate": 90}), media_id=version["media_id"]
    )
    assert client.get(root + "/workbench/references").json()["items"][0]["id"] == newer["id"]
    detail = root + f"/workbench/references/{version['id']}?source_type=media_version"
    assert client.get(detail).json()["blob_hash"] == version["original_hash"]
    seen = []
    submit = MockProvider.submit

    def capture(provider, request):
        seen.extend(request.references)
        return submit(provider, request)

    monkeypatch.setattr(MockProvider, "submit", capture)
    payload = body(references=[source])
    assert client.post(root + "/workbench/estimate", json=payload).status_code == 200
    job = post_ok(client, root + "/workbench/generate", payload)
    assert job["status"] == "succeeded", job["error"]
    assert seen[0].blob_hash == version["original_hash"] and seen[0].kind == "image"
    assert job["input_snapshot"]["reference_sources"] == [source]
    assert post_ok(client, root + "/workbench/generate", payload)["id"] == job["id"]
    for invalid in ({}, {**source, "generation_id": str(uuid.uuid4())}):
        assert (
            client.post(root + "/workbench/estimate", json=body(references=[invalid])).status_code
            == 422
        )


def test_uploaded_video_and_image_reach_video_wire_without_index(
    uploaded, changing_video, monkeypatch
):
    from tests.test_model_channels import add, setup

    client, sessions, ids, root, put = uploaded
    client.base_url = "https://studio.example.com"
    image, video = put(media_engine.mock_image()), put(changing_video, "动作.mp4")
    assert {
        x["kind"] for x in client.get(root + "/workbench/references?media_type=all").json()["items"]
    } == {"image", "video"}
    assert {x["kind"] for x in client.get(root + "/workbench/references").json()["items"]} == {
        "image"
    }
    _, channel = setup(client, ids["project"])
    profile = add(
        client,
        root,
        channel,
        name="参考视频",
        model="S-2.5-满血",
        protocol="video_v1",
        modality="video",
        max_reference_images=1,
        max_reference_videos=1,
        parameters={"duration": [4], "resolution": ["720p"]},
    )
    calls = []

    def wire(provider, method, path, **kwargs):
        calls.append(kwargs["json"])
        return {"id": "no-index-reference"}

    monkeypatch.setattr(VideoV1Provider, "_request", wire)
    monkeypatch.setattr(
        VideoV1Provider,
        "poll",
        lambda *args: GenerationResult(
            status="succeeded", outputs=[Output(type="video", data=changing_video)]
        ),
    )
    for sources, operation in [([image], "image_to_video"), ([image, video], "video_to_video")]:
        result = post_ok(
            client,
            root + "/workbench/generate",
            body(
                provider=profile["provider_name"],
                request_type="video",
                provider_params={"duration": 4, "resolution": "720p"},
                references=[{"media_version_id": v["id"], "role": "reference"} for v in sources],
            ),
        )
        assert result["status"] == "succeeded", result["error"]
        assert calls[-1]["operation"] == operation
        for material, source in zip(calls[-1]["media_inputs"], sources, strict=True):
            # Provider-accessible links contain the pinned original bytes.
            data = client.get(material["url"])
            assert data.status_code == 200
            assert hashlib.sha256(data.content).hexdigest() == source["original_hash"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(MediaIndex)) == 0


def test_selection_permissions_and_queued_retry_revalidate_uploads(uploaded, monkeypatch):
    client, sessions, ids, root, put = uploaded
    v = put(media_engine.mock_image())
    ref = {"media_version_id": v["id"], "role": "reference"}
    detail = root + f"/workbench/references/{v['id']}?source_type=media_version"
    reference_caps(monkeypatch)
    execute = jobs.execute
    monkeypatch.setattr(jobs, "execute", lambda *_: None)
    queued = post_ok(client, root + "/workbench/generate", body(references=[ref]))
    assert queued["status"] == "pending"
    for field, value, expected in [
        ("status", "processing", 409),
        ("status", "failed", 409),
        ("deleted_at", datetime.now(UTC), 404),
        ("project_id", ids["other"], 404),
    ]:
        with sessions() as db:
            record = (
                db.get(MediaVersion, uuid.UUID(v["id"]))
                if field == "status"
                else db.get(LibraryMedia, uuid.UUID(v["media_id"]))
            )
            old = getattr(record, field)
            setattr(record, field, value)
            db.commit()
        assert client.get(detail).status_code == expected
        assert client.get(root + "/workbench/references").json()["items"] == []
        assert (
            client.post(root + "/workbench/estimate", json=body(references=[ref])).status_code
            == expected
        )
        with sessions() as db:
            record = (
                db.get(MediaVersion, uuid.UUID(v["id"]))
                if field == "status"
                else db.get(LibraryMedia, uuid.UUID(v["media_id"]))
            )
            setattr(record, field, old)
            db.commit()
    with sessions() as db:
        db.get(LibraryMedia, uuid.UUID(v["media_id"])).deleted_at = datetime.now(UTC)
        db.commit()
        execute(db, uuid.UUID(queued["id"]))
        job = db.get(GenerationJob, uuid.UUID(queued["id"]), populate_existing=True)
        assert job.status == "failed" and not job.external_job_id
    assert client.post(root + f"/jobs/{queued['id']}/retry").status_code == 404


def test_catalog_search_and_pagination_include_uploaded_metadata(uploaded):
    client, sessions, ids, root, put = uploaded
    v = put(media_engine.mock_image())
    with sessions() as db:
        for n in range(26):
            media = LibraryMedia(
                project_id=uuid.UUID(ids["project"]),
                name=f"人物 {n}",
                description="雨夜",
                tags=["参考"],
            )
            db.add(media)
            db.flush()
            db.add(
                MediaVersion(
                    media_id=media.id,
                    ordinal=1,
                    filename="source.png",
                    media_type="image",
                    status="ready",
                    fingerprint=v["original_hash"],
                    original_hash=v["original_hash"],
                    size_bytes=v["size_bytes"],
                )
            )
        db.commit()
    for query in ("人物", "雨夜", "参考"):
        first = client.get(root + "/workbench/references", params={"query": query}).json()
        second = client.get(
            root + "/workbench/references", params={"query": query, "offset": 24}
        ).json()
        assert len(first["items"]) == 24 and first["has_more"]
        assert len(second["items"]) == (3 if query == "参考" else 2) and not second["has_more"]
        assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    assert client.get(root + "/workbench/references", params={"query": "%"}).json()["items"] == []


@pytest.mark.parametrize("revoked", [False, True])
def test_harness_direct_media_context_preserves_uploaded_version(uploaded, monkeypatch, revoked):
    from app.modules.harness import runtime, service
    from tests.test_harness_conversation import conversation, start

    _, sessions, ids, _, put = uploaded
    v = put(media_engine.mock_image())
    reference_caps(monkeypatch)
    monkeypatch.setattr(settings, "production_harness_enabled", True)
    if revoked:
        submit = MockProvider.submit

        def revoke(provider, request):
            with sessions() as db:
                db.get(LibraryMedia, uuid.UUID(v["media_id"])).deleted_at = datetime.now(UTC)
                db.commit()
            return submit(provider, request)

        monkeypatch.setattr(MockProvider, "submit", revoke)
    with sessions() as db:
        message, run, _ = start(
            db,
            ids,
            conversation(db, ids),
            mode="standard",
            media={
                "provider": "mock",
                "request_type": "image",
                "references": [{"media_version_id": v["id"]}],
            },
        )
        reference = run.context["references"][0]
        assert reference["kind"] == "media_version" and reference["id"] == v["id"]
        task = service.tasks(db, run)[0]
        assert task.specification["arguments"]["references"][0]["media_version_id"] == v["id"]
        ticket = service.claim(db, run.id)
        db.commit()
    runtime.execute(ticket, sessions)
    with sessions() as db:
        job = db.scalar(select(GenerationJob))
        assert job, "no job created"
        if revoked:
            assert job.status == "failed" and job.error
        else:
            assert job.status == "succeeded", job.error
        assert job.input_snapshot["references"][0]["blob_hash"] == v["original_hash"]
