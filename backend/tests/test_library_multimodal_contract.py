"""Real files through upload, immutable references, reuse and creative extraction."""

# ruff: noqa: F811
import hashlib
import io
import math
import struct
import uuid
import wave

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import settings
from app.models.generation import Generation
from app.models.identity import Membership
from app.models.library import MediaUsage
from app.models.storage import Blob
from app.modules.library import worker
from app.storage import cas
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.fixture
def multimedia(studio, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "library_staging_dir", str(tmp_path / "staging"))
    monkeypatch.setattr(worker, "SessionLocal", studio[1])
    return studio


def content(kind):
    output = io.BytesIO()
    if kind == "image":
        Image.new("RGBA", (32, 16), (10, 80, 200, 128)).save(output, "PNG")
        return "picture.png", output.getvalue()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(
            b"".join(
                struct.pack("<h", round(6000 * math.sin(i * 2 * math.pi * 440 / 8000)))
                for i in range(16000)
            )
        )
    return "sound.wav", output.getvalue()


def upload(client, base, filename, data, media_id=None):
    version = post_ok(
        client,
        f"{base}/uploads",
        {
            "filename": filename,
            "size_bytes": len(data),
            "fingerprint": hashlib.sha256(data).hexdigest(),
            "media_id": media_id,
        },
    )
    for offset in range(0, len(data), version["chunk_size"]):
        response = client.post(
            f"{base}/versions/{version['id']}/chunk?offset={offset}",
            files={"file": ("chunk", data[offset : offset + version["chunk_size"]])},
        )
        assert response.status_code == 200, response.text
    post_ok(client, f"{base}/versions/{version['id']}/complete")
    worker.process(uuid.UUID(version["id"]))
    version = client.get(f"{base}/versions/{version['id']}").json()
    assert version["status"] == "ready", version
    return version


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_typed_upload_reference_materialization_and_immutable_versions(multimedia, kind):
    client, sessions, ids = multimedia
    root = f"/api/v1/projects/{ids['project']}"
    base = root + "/library"
    filename, data = content(kind)
    version = upload(client, base, filename, data)
    assert version["media_type"] == kind
    assert version["has_audio"] is (kind == "audio")
    assert version["original_hash"] == hashlib.sha256(data).hexdigest()
    for selected_kind in ["video", "image", "audio"]:
        assert client.get(f"{base}/catalog?media_type={selected_kind}").json()["total"] == int(
            selected_kind == kind
        )
        assert client.get(f"{base}/version-catalog?media_type={selected_kind}").json()[
            "total"
        ] == int(selected_kind == kind)
    request = {"version_id": version["id"], "shot_id": ids["shot"]}
    if kind == "audio":
        assert client.post(f"{base}/usages", json=request).status_code == 422
        request.update(start_ms=250, end_ms=1000)
        assert client.post(f"{base}/usages", json={**request, "end_ms": None}).status_code == 422
    else:
        assert version["duration_ms"] is None
        assert (
            client.post(
                f"{base}/usages", json={**request, "start_ms": 0, "end_ms": 1000}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"{base}/versions/{version['id']}/subtitles",
                json={"content": "1\n00:00:00,000 --> 00:00:01,000\nword\n"},
            ).status_code
            == 422
        )
    usage = post_ok(client, f"{base}/usages", request)
    assert usage["media_type"] == kind
    assert post_ok(client, f"{base}/usages", request)["id"] == usage["id"]
    with sessions() as db:
        assert len(db.scalars(select(MediaUsage)).all()) == 1
    assert usage["start_ms"] == (None if kind == "image" else 250)
    assert client.post(f"{base}/{version['media_id']}/trash").status_code == 409
    output_request = {"request_key": str(uuid.uuid4()), "output_type": kind}
    endpoint = f"{base}/usages/{usage['id']}/materialize"
    assert client.post(endpoint, json={**output_request, "output_type": "video"}).status_code == 422
    job = post_ok(client, endpoint, output_request)
    assert job["status"] == "succeeded", job
    assert post_ok(client, endpoint, output_request)["id"] == job["id"]
    with sessions() as db:
        result = db.scalar(select(Generation).where(Generation.job_id == uuid.UUID(job["id"])))
        assert result.output_type == kind
        origin = result.input_refs["library_origin"]
        assert origin["version_id"] == version["id"] and origin["media_type"] == kind
        if kind == "image":
            assert origin["start_ms"] is None and origin["end_ms"] is None
            with cas.open_range(db.get(Blob, result.output_blob_hash)) as stream:
                picture = Image.open(io.BytesIO(stream.read()))
                assert picture.size == (32, 16) and picture.getpixel((1, 1)) == (10, 80, 200, 128)
        else:
            actual = result.input_refs["actual_media"]
            assert actual["has_audio"] and not actual["has_video"]
            assert 700 <= actual["duration_ms"] <= 900
    wrong_filename = "other.wav" if kind == "image" else "other.png"
    assert (
        client.post(
            f"{base}/uploads",
            json={
                "filename": wrong_filename,
                "size_bytes": 12,
                "fingerprint": "a" * 64,
                "media_id": version["media_id"],
            },
        ).status_code
        == 409
    )
    second = upload(client, base, filename, data, version["media_id"])
    assert second["ordinal"] == 2 and second["id"] != version["id"]
    assert (
        client.get(f"{base}/usages?shot_id={ids['shot']}").json()[0]["version_id"] == version["id"]
    )


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_multimodal_reuse_enforces_access_and_retains_type_and_source(multimedia, kind):
    client, sessions, ids = multimedia
    base = f"/api/v1/projects/{ids['project']}/library"
    other = f"/api/v1/projects/{ids['other']}/library"
    filename, data = content(kind)
    source = upload(client, base, filename, data)
    body = {"source_project_id": ids["project"], "source_version_id": source["id"]}
    assert client.post(other + "/reuse", json=body).status_code == 403
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    # Membership in another project must not reveal the source under that project's URL.
    assert client.get(other + f"/versions/{source['id']}").status_code == 404
    result = post_ok(client, other + "/reuse", body)
    target = client.get(other + f"/versions/{result['version_id']}").json()
    assert target["media_type"] == kind
    for field in [
        "original_hash",
        "proxy_hash",
        "poster_hash",
        "duration_ms",
        "width",
        "height",
        "has_audio",
    ]:
        assert target[field] == source[field]
    assert client.post(f"{base}/{source['media_id']}/trash").status_code == 409
    post_ok(client, other + f"/{target['media_id']}/trash")
    assert client.get(other + f"/versions/{target['id']}").status_code == 404
    assert client.get(base + f"/versions/{source['id']}").status_code == 200
    post_ok(client, other + f"/{target['media_id']}/restore")
    assert client.get(other + f"/versions/{target['id']}").json()["media_type"] == kind
