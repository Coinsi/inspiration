"""Real decoding/encoding tests plus opt-in PostgreSQL API tests in isolated schemas.

TEST_DATABASE_URL enables integration checks; only randomly named test schemas are changed.
"""

import io
import os
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.adapters.contracts import GenerationResult, Output
from app.adapters.generation.mock import MockProvider
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models import Base
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership, Project, User
from app.models.narrative import Scene, Script
from app.models.shot import Shot
from app.modules.generation import jobs, media_engine


def test_crop_rotate_resize_pixels():
    im = Image.new("RGB", (80, 40), "red")
    for x in range(40, 80):
        for y in range(40):
            im.putpixel((x, y), (0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    out = media_engine.transform_image(
        buf.getvalue(), {"crop": [0, 0, 0.5, 1], "rotate": 90, "scale": 2}
    )
    with Image.open(io.BytesIO(out)) as actual:
        assert actual.size == (80, 80)
        assert actual.getpixel((40, 40)) == (255, 0, 0)
    with pytest.raises(ValueError):
        media_engine.transform_image(buf.getvalue(), {"crop": [0.9, 0, 0.5, 1]})
    transparent = io.BytesIO()
    Image.new("RGBA", (4, 4), (50, 100, 150, 64)).save(transparent, "PNG")
    adjusted = Image.open(
        io.BytesIO(media_engine.transform_image(transparent.getvalue(), {"brightness": 1.2}))
    )
    assert adjusted.getpixel((0, 0)) == (60, 120, 180, 64)


def test_render_mixed_media_audio_and_cancel(tmp_path):
    source = tmp_path / "source.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
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
    blobs = {"image": media_engine.mock_image(), "video": source.read_bytes()}
    clips = [
        {"blob_hash": "image", "output_type": "image", "duration_ms": 500},
        {"blob_hash": "video", "output_type": "video", "in_point_ms": 200, "duration_ms": 1500},
    ]
    result = media_engine.render(clips, blobs.__getitem__, {"height": 720, "aspect_ratio": "9:16"})
    output = tmp_path / "film.mp4"
    output.write_bytes(result)
    probe = media_engine.run(["-i", str(output), "-f", "null", "-"])
    assert "720x1280" in probe and "Audio:" in probe
    assert "00:00:02." in probe
    assert result[4:8] == b"ftyp"
    with pytest.raises(media_engine.Canceled):
        media_engine.render(clips, blobs.__getitem__, {}, lambda: True)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for isolated PostgreSQL integration checks")
    admin = create_engine(url)
    schema = "test_media_" + uuid.uuid4().hex
    with admin.begin() as conn:
        conn.execute(CreateSchema(schema))
    engine = admin.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine, checkfirst=False)
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(jobs, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "storage_backend", "fs")
    monkeypatch.setattr(settings, "fs_storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "celery_eager", True)
    monkeypatch.setattr(settings, "secret_key", "isolated-media-tests-only-secret-key-32-bytes")

    def db_override():
        with sessions() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    app.dependency_overrides[get_db] = db_override
    with sessions() as db:
        user = User(
            username="media_test",
            email="media@example.com",
            password_hash="unused",
            display_name="Media Test",
        )
        db.add(user)
        db.flush()
        p = Project(code="TEST", name="Media workflow test", owner_id=user.id)
        other = Project(code="OTHER", name="Other project", owner_id=user.id)
        db.add_all([p, other])
        db.flush()
        db.add(Membership(project_id=p.id, user_id=user.id, role="admin"))
        script = Script(project_id=p.id, code="SCR-1", title="Test script")
        db.add(script)
        db.flush()
        scene = Scene(
            project_id=p.id, script_id=script.id, code="SC-1", ordinal=1, title="Test scene"
        )
        db.add(scene)
        db.flush()
        shot = Shot(project_id=p.id, scene_id=scene.id, code="SH-1", ordinal=1, title="Test shot")
        db.add(shot)
        db.flush()
        db.commit()
        ids = dict(project=str(p.id), shot=str(shot.id), user=user.id, other=other.id)
        token = create_access_token(str(user.id))
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {token}"
            yield client, sessions, ids
    finally:
        app.dependency_overrides.pop(get_db, None)
        # Fixed prefix + UUID constructed here, never a user-supplied schema.
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def post_ok(client, path, data=None):
    r = client.post(path, json=data)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def changing_video(tmp_path):
    path = tmp_path / "changing.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-threads",
            "2",
            str(path),
        ]
    )
    return path.read_bytes()


def test_video_tools_frames_audio_and_trim(changing_video, tmp_path):
    import wave
    from array import array

    from app.modules.generation.video_tools import probe_file, process

    frames = process(changing_video, {"operation": "frames", "times_ms": [200, 1200]})
    for (data, typ, info), channel in zip(frames, [0, 2]):
        im = Image.open(io.BytesIO(data))
        pixel = im.getpixel((160, 90))
        assert typ == "image" and im.size == (320, 180)
        assert pixel[channel] > 240 and sum(pixel) < 270
        assert info["source_time_ms"] in (200, 1200)
    for operation, typ in [("audio", "audio"), ("trim", "video")]:
        data, actual_type, info = process(
            changing_video, {"operation": operation, "start_ms": 1100, "end_ms": 1800}
        )[0]
        assert actual_type == typ and info["has_audio"]
        assert abs(info["duration_ms"] - 700) < 100
        path = tmp_path / f"{operation}.mp4"
        path.write_bytes(data)
        assert probe_file(path)["has_video"] == (typ == "video")
        wav = tmp_path / f"{operation}.wav"
        media_engine.run(["-i", str(path), "-vn", "-c:a", "pcm_s16le", str(wav)])
        with wave.open(str(wav)) as sound:
            samples = array("h", sound.readframes(sound.getnframes()))
            assert max(abs(s) for s in samples) > 2000  # audible signal, not invented silence
        if typ == "video":
            frame = process(data, {"operation": "frames", "times_ms": [0]})[0][0]
            assert Image.open(io.BytesIO(frame)).getpixel((160, 90))[2] > 240
    with pytest.raises(media_engine.Canceled):
        process(changing_video, {"operation": "frames", "times_ms": [0]}, lambda: True)


def test_video_tools_invalid_and_silent_inputs(changing_video, tmp_path):
    from app.modules.generation.video_tools import process

    for options in [
        {"operation": "frames", "times_ms": []},
        {"operation": "frames", "times_ms": [200, 200]},
        {"operation": "frames", "times_ms": [2000]},
        {"operation": "trim", "start_ms": 1500, "end_ms": 2200},
        {"operation": "audio", "start_ms": 1000, "end_ms": 900},
    ]:
        with pytest.raises(ValueError):
            process(changing_video, options)
    with pytest.raises(ValueError):
        process(b"not video", {"operation": "frames", "times_ms": [0]})
    src, silent = tmp_path / "src.mp4", tmp_path / "silent.mp4"
    src.write_bytes(changing_video)
    media_engine.run(["-i", str(src), "-an", "-c:v", "copy", str(silent)])
    with pytest.raises(ValueError, match="没有音轨"):
        process(silent.read_bytes(), {"operation": "audio", "start_ms": 0, "end_ms": 1000})
    trimmed = process(silent.read_bytes(), {"operation": "trim", "start_ms": 0, "end_ms": 1000})
    assert trimmed[0][2]["has_audio"] is False


def test_video_tools_api_provenance_download_and_guards(studio, changing_video):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    response = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("source.mp4", changing_video, "video/mp4")},
    )
    assert response.status_code == 200, response.text
    original = response.json()
    url = f"{base}/generations/{original['id']}"
    info = client.get(url + "/video-info")
    assert info.status_code == 200 and info.json()["duration_ms"] == 2000
    for operation, output_type in [("frames", "image"), ("audio", "audio"), ("trim", "video")]:
        job = post_ok(
            client,
            url + "/video-tools",
            {"operation": operation, "times_ms": [200, 1200], "start_ms": 100, "end_ms": 1600},
        )
        assert job["status"] == "succeeded", job
        assert job["actual_cost"] == 0
        gens = client.get(f"{base}/generations?target_type=shot&target_id={ids['shot']}").json()
        outputs = [g for g in gens if g["job_id"] == job["id"]]
        assert len(outputs) == (2 if operation == "frames" else 1)
        assert all(
            g["output_type"] == output_type
            and g["input_refs"]["source_generation_id"] == original["id"]
            for g in outputs
        )
        if operation == "audio":
            audio = outputs[0]
            blob_url = f"{base}/blobs/{audio['output_blob_hash']}"
            download = client.get(blob_url + "?download=true")
            assert download.headers["content-type"] == "audio/mp4"
            assert ".m4a" in download.headers["content-disposition"]
            assert (
                client.get(blob_url, headers={"Range": "bytes=0-31"}).content
                == download.content[:32]
            )
            assert client.post(f"{base}/generations/{audio['id']}/select").status_code == 422
            timeline = post_ok(client, base + "/timelines", {"name": "No audio as visuals"})
            assert (
                client.put(
                    f"{base}/timelines/{timeline['id']}/items",
                    json={
                        "items": [
                            {
                                "shot_id": ids["shot"],
                                "generation_id": audio["id"],
                                "duration_ms": 1000,
                            }
                        ]
                    },
                ).status_code
                == 422
            )
    failed = post_ok(client, url + "/video-tools", {"operation": "frames", "times_ms": [2500]})
    assert failed["status"] == "failed" and "结束时间" in failed["error"]
    assert (
        client.post(
            url + "/video-tools", json={"operation": "frames", "times_ms": [-1]}
        ).status_code
        == 422
    )
    assert client.get(f"{base}/generations/{uuid.uuid4()}/video-info").status_code == 404
    with sessions() as db:
        db.get(Generation, uuid.UUID(original["id"])).project_id = ids["other"]
        db.commit()
    assert client.get(url + "/video-info").status_code == 404
    assert (
        client.post(url + "/video-tools", json={"operation": "frames", "times_ms": [0]}).status_code
        == 404
    )
    with sessions() as db:
        db.get(Generation, uuid.UUID(original["id"])).project_id = uuid.UUID(ids["project"])
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert client.get(url + "/video-info").status_code == 200
    assert (
        client.post(url + "/video-tools", json={"operation": "frames", "times_ms": [0]}).status_code
        == 403
    )


def test_video_tools_timeline_requires_director(studio, changing_video):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    original = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("video.mp4", changing_video, "video/mp4")},
    ).json()
    timeline = post_ok(client, base + "/timelines", {"name": "Timeline permissions"})
    with sessions() as db:
        g = db.get(Generation, uuid.UUID(original["id"]))
        g.target_type, g.target_id = "timeline", uuid.UUID(timeline["id"])
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "artist"
        db.commit()
    url = f"{base}/generations/{original['id']}"
    assert client.get(url + "/video-info").status_code == 200
    assert (
        client.post(url + "/video-tools", json={"operation": "frames", "times_ms": [0]}).status_code
        == 403
    )
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "director"
        db.commit()
    job = post_ok(client, url + "/video-tools", {"operation": "frames", "times_ms": [0]})
    assert job["status"] == "succeeded" and job["target_type"] == "timeline"


def test_character_presets_preview_is_free_and_validates_combinations(studio, monkeypatch):
    from app.adapters.generation.gpt_image import GptImageProvider
    from app.modules.generation import service

    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    source = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("ref.png", media_engine.mock_image(), "image/png")},
    ).json()
    provider = GptImageProvider(token="test")
    monkeypatch.setattr(service, "_provider_instance", lambda *args: provider)
    monkeypatch.setattr(
        provider, "submit", lambda *_: pytest.fail("Preview must not invoke generation")
    )
    catalog = client.get(base + "/character-presets").json()
    assert sum(map(len, catalog["groups"].values())) == 12
    url = f"{base}/generations/{source['id']}"
    for mode, presets in catalog["groups"].items():
        for preset in presets:
            result = post_ok(
                client,
                url + "/character-preview",
                {
                    "provider": "gpt_image",
                    "mode": mode,
                    "preset": preset["id"],
                    "notes": "保留金色耳环",
                },
            )
            assert result["count"] == 1 and result["points"] == 30
            assert (
                result["source_generation_id"] == source["id"]
                and "保留金色耳环" in result["prompt"]
            )
    invalid = {"provider": "gpt_image", "mode": "view", "preset": "happy"}
    assert client.post(url + "/character-preview", json=invalid).status_code == 422
    assert client.post(url + "/character", json=invalid).status_code == 422
    assert (
        client.post(url + "/character-preview", json={**invalid, "notes": "x" * 2001}).status_code
        == 422
    )
    with sessions() as db:
        assert len(list(db.scalars(select(GenerationJob)))) == 1  # upload only


def test_character_generation_transmits_reference_and_retains_retry_provenance(studio, monkeypatch):
    import base64

    from app.adapters.generation.gpt_image import GptImageProvider
    from app.modules.generation import service

    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    reference = media_engine.mock_image()
    source = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("ref.png", reference, "image/png")},
    ).json()
    provider = GptImageProvider(token="test")
    requests = []

    def reply(req):
        requests.append(req)
        return {"data": [{"b64_json": base64.b64encode(media_engine.mock_image(1)).decode()}]}

    monkeypatch.setattr(provider, "_http", reply)
    monkeypatch.setattr(service, "_provider_instance", lambda *args: provider)
    url = f"{base}/generations/{source['id']}"
    for mode, preset in [
        ("view", "profile_left"),
        ("expression", "happy"),
        ("sheet", "three_view"),
    ]:
        body = {"provider": "gpt_image", "mode": mode, "preset": preset}
        preview = post_ok(client, url + "/character-preview", body)
        job = post_ok(client, url + "/character", body)
        assert job["status"] == "succeeded", job
        assert job["input_snapshot"]["prompt"] == preview["prompt"]
        assert job["input_snapshot"]["references"] == [
            {"blob_hash": source["output_blob_hash"], "role": "ref"}
        ]
        assert b'name="image[]"' in requests[-1].data and reference in requests[-1].data
        assert preview["prompt"].encode() in requests[-1].data and requests[-1].full_url.endswith(
            "/images/edits"
        )
        outputs = client.get(f"{base}/generations?target_type=shot&target_id={ids['shot']}").json()
        result = next(g for g in outputs if g["job_id"] == job["id"])
        assert result["input_refs"]["character_preset"]["preset"] == preset
        assert result["input_refs"]["source_generation_id"] == source["id"]
        assert not result["is_selected"] and result["cost_points"] == preview["points"]
    monkeypatch.setattr(
        provider, "submit", lambda *_: (_ for _ in ()).throw(ValueError("provider offline"))
    )
    failed = post_ok(client, url + "/character", body)
    assert failed["status"] == "failed"
    monkeypatch.delattr(provider, "submit")
    retried = post_ok(client, base + f"/jobs/{failed['id']}/retry")
    assert retried["status"] == "succeeded"
    assert (
        retried["input_snapshot"]["character_preset"]
        == failed["input_snapshot"]["character_preset"]
    )
    assert retried["input_snapshot"]["prompt"] == failed["input_snapshot"]["prompt"]


def test_character_guards_provider_source_scope_and_permissions(studio, monkeypatch):
    from app.adapters.generation.gpt_image import GptImageProvider
    from app.models.generation import Quota
    from app.modules.generation import service

    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    source = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("ref.png", media_engine.mock_image(), "image/png")},
    ).json()
    url = f"{base}/generations/{source['id']}"
    body = {"provider": "mock", "mode": "expression", "preset": "happy"}
    assert client.post(url + "/character", json=body).status_code == 422
    monkeypatch.setattr(service, "_provider_instance", lambda *args: GptImageProvider(token="test"))
    body["provider"] = "gpt_image"
    with sessions() as db:
        g = db.get(Generation, uuid.UUID(source["id"]))
        g.output_type = "video"
        db.commit()
    assert client.post(url + "/character-preview", json=body).status_code == 422
    with sessions() as db:
        g = db.get(Generation, uuid.UUID(source["id"]))
        g.output_type, g.project_id = "image", ids["other"]
        db.commit()
    assert client.post(url + "/character", json=body).status_code == 404
    with sessions() as db:
        db.get(Generation, uuid.UUID(source["id"])).project_id = uuid.UUID(ids["project"])
        db.add(
            Quota(project_id=uuid.UUID(ids["project"]), scope="project", limit_cost=1, used_cost=0)
        )
        db.commit()
    assert client.post(url + "/character", json=body).status_code == 402
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert client.post(url + "/character-preview", json=body).status_code == 403
    assert client.post(url + "/character", json=body).status_code == 403


def test_inpaint_mask_provenance_and_project_guards(studio, monkeypatch):
    import base64

    from app.adapters.generation.gpt_image import GptImageProvider
    from app.models.storage import Blob
    from app.modules.generation import service
    from app.storage import cas

    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    original = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("source.png", media_engine.mock_image(), "image/png")},
    ).json()
    body = {
        "provider": "gpt_image",
        "prompt": "Change the selected area to blue",
        "strokes": [{"points": [[0.5, 0.5]], "radius": 0.1}],
    }
    provider = GptImageProvider(token="test", config={"model": "gpt-image-2"})
    captured = []
    monkeypatch.setattr(
        provider,
        "_http",
        lambda req: (
            captured.append(req.data)
            or {"data": [{"b64_json": base64.b64encode(media_engine.mock_image()).decode()}]}
        ),
    )
    monkeypatch.setattr(service, "_provider_instance", lambda *args: provider)
    job = post_ok(client, f"{base}/generations/{original['id']}/inpaint", body)
    assert job["status"] == "succeeded", job
    assert job["input_snapshot"]["operation"] == "inpaint"
    assert len(captured) == 1 and b'name="mask"' in captured[0]
    variants = client.get(f"{base}/generations?target_type=shot&target_id={ids['shot']}").json()
    edited = next(g for g in variants if g["job_id"] == job["id"])
    assert edited["id"] != original["id"]
    assert edited["input_refs"]["edit_source_id"] == original["id"]
    with sessions() as db:
        blob = db.get(Blob, job["input_snapshot"]["mask"]["blob_hash"])
        mask = Image.open(io.BytesIO(cas.read_bytes(blob)))
        assert mask.size == (640, 360)
        assert mask.getpixel((320, 180))[3] == 0
        assert mask.getpixel((0, 0))[3] == 255
        g = db.get(Generation, uuid.UUID(original["id"]))
        g.project_id = ids["other"]
        db.commit()
    assert client.post(f"{base}/generations/{original['id']}/inpaint", json=body).status_code == 404


def test_inpaint_rejects_unsupported_empty_and_invalid_selection(studio, monkeypatch):
    from app.modules.generation import service

    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    original = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("source.png", media_engine.mock_image(), "image/png")},
    ).json()
    monkeypatch.setattr(service, "_provider_instance", lambda *args: MockProvider())
    path = f"{base}/generations/{original['id']}/inpaint"
    body = {
        "provider": "mock",
        "prompt": "test",
        "strokes": [{"points": [[0.5, 0.5]], "radius": 0.1}],
    }
    assert client.post(path, json=body).status_code == 422
    assert client.post(path, json={**body, "strokes": []}).status_code == 422
    assert client.post(path, json={**body, "prompt": " "}).status_code == 422
    with sessions() as db:
        membership = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        membership.role = "viewer"
        db.commit()
    assert client.post(path, json=body).status_code == 403
    assert client.post(f"{base}/prompts/optimize", json={"prompt": "test"}).status_code == 403


def test_optimizer_requires_config_and_valid_json_without_changing_source(studio, monkeypatch):
    import json

    from app.core.crypto import encrypt
    from app.models.generation import ProviderConfig
    from app.modules.generation import editing

    client, sessions, ids = studio
    path = f"/api/v1/projects/{ids['project']}/prompts/optimize"
    body = {"prompt": "红色茶壶", "mode": "refine"}
    assert client.post(path, json=body).status_code == 422
    with sessions() as db:
        db.add(
            ProviderConfig(
                project_id=uuid.UUID(ids["project"]),
                provider_name="cloud_llm",
                kind="llm",
                enabled=True,
                endpoint="https://example.invalid/v1",
                credentials_encrypted=encrypt("test"),
                config={"model": "test"},
            )
        )
        db.commit()
    sent = []

    def reply(system, user, *args):
        sent.append(json.loads(user))
        return json.dumps(
            {
                "prompt": "红色茶壶，灰色背景",
                "avoid": "文字水印",
                "explanation": "补充背景",
                "assumptions": ["灰色背景"],
            }
        )

    monkeypatch.setattr(editing, "_chat", reply)
    result = post_ok(client, path, body)
    assert result["prompt"] == "红色茶壶，灰色背景" and sent[0]["prompt"] == body["prompt"]
    assert client.post(path, json={**body, "mode": "style"}).status_code == 422
    monkeypatch.setattr(editing, "_chat", lambda *args: "not json")
    assert client.post(path, json=body).status_code == 502
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "Test shot"
        assert list(db.scalars(select(GenerationJob))) == []


def test_end_to_end_refine_render_and_range(studio, tmp_path):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    r = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("source.png", media_engine.mock_image(), "image/png")},
    )
    assert r.status_code == 200, r.text
    original = r.json()
    refined = post_ok(
        client,
        f"{base}/generations/{original['id']}/refine",
        {"crop": [0, 0, 0.5, 1], "rotate": 90},
    )
    assert refined["status"] == "succeeded", refined
    variants = client.get(f"{base}/generations?target_type=shot&target_id={ids['shot']}").json()
    g = next(g for g in variants if g["job_id"] == refined["id"])
    image = client.get(f"{base}/blobs/{g['output_blob_hash']}")
    assert Image.open(io.BytesIO(image.content)).size == (360, 320)
    assert original["output_blob_hash"] != g["output_blob_hash"]
    post_ok(client, f"{base}/generations/{g['id']}/select")
    timeline = post_ok(client, f"{base}/timelines", {"name": "Test cut"})
    r = client.put(
        f"{base}/timelines/{timeline['id']}/items",
        json={"items": [{"shot_id": ids["shot"], "generation_id": g["id"], "duration_ms": 1000}]},
    )
    assert r.status_code == 200, r.text
    rendered = post_ok(client, f"{base}/timelines/{timeline['id']}/render", {"height": 720})
    assert rendered["status"] == "succeeded", rendered
    films = client.get(f"{base}/generations?target_type=timeline&target_id={timeline['id']}").json()
    url = f"{base}/blobs/{films[0]['output_blob_hash']}"
    full = client.get(url)
    assert full.content[4:8] == b"ftyp"
    partial = client.get(url, headers={"Range": "bytes=0-31"})
    assert partial.status_code == 206 and partial.content == full.content[:32]
    assert "attachment" in client.get(url + "?download=true").headers["content-disposition"]
    assert client.get(url, headers={"Range": "bytes=9999999999-"}).status_code == 416
    all_jobs = client.get(base + "/jobs").json()
    assert {j["input_snapshot"]["operation"] for j in all_jobs} == {"upload", "refine", "render"}


def test_scope_input_and_capability_guards(studio):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    random_id = uuid.uuid4()
    # Prompt overrides must not bypass target ownership checks.
    r = client.post(f"{base}/shots/{random_id}/generate", json={"prompt_override": "test"})
    assert r.status_code == 404
    r = client.post(
        f"{base}/shots/{ids['shot']}/generate",
        json={"request_type": "video", "provider_params": {"duration": 999}},
    )
    assert r.status_code == 422
    r = client.post(
        f"{base}/shots/{ids['shot']}/generate",
        json={"first_frame_id": str(random_id), "request_type": "video"},
    )
    assert r.status_code == 422
    with sessions() as db:
        foreign = GenerationJob(
            project_id=ids["other"],
            target_type="shot",
            target_id=random_id,
            provider="mock",
            request_type="image",
            status="failed",
        )
        db.add(foreign)
        db.flush()
        foreign_id = foreign.id
        db.commit()
        from app.storage import cas

        blob = cas.put_bytes(db, media_engine.mock_image(3), "image/png")
        db.add(
            Generation(
                project_id=ids["other"],
                job_id=foreign_id,
                target_id=random_id,
                target_type="shot",
                provider="mock",
                output_type="image",
                output_blob_hash=blob.hash,
            )
        )
        foreign_hash = blob.hash
        db.commit()
    assert client.get(f"{base}/blobs/{foreign_hash}").status_code == 404
    assert client.get("/api/v1/me").status_code == 200
    assert client.get(f"{base}/jobs/{foreign_id}").status_code == 404
    assert client.post(f"{base}/jobs/{foreign_id}/retry").status_code == 404
    assert client.post(f"{base}/jobs/{foreign_id}/cancel").status_code == 404
    timeline = post_ok(client, f"{base}/timelines", {"name": "Guard test"})
    assert (
        client.put(
            f"{base}/timelines/{timeline['id']}/items",
            json={"items": [{"shot_id": str(random_id)}]},
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"{base}/timelines/{timeline['id']}/items",
            json={"items": [{"shot_id": ids["shot"], "in_point_ms": -1}]},
        ).status_code
        == 422
    )
    assert client.post(f"{base}/timelines/{timeline['id']}/render", json={}).status_code == 422


def test_quota_includes_pending_jobs(studio, monkeypatch):
    client, sessions, ids = studio
    from app.models.generation import Quota

    with sessions() as db:
        db.add(
            Quota(project_id=uuid.UUID(ids["project"]), limit_cost=10, used_cost=0, scope="project")
        )
        pending = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            request_type="image",
            status="pending",
            estimated_cost=10,
        )
        db.add(pending)
        db.commit()
    base = f"/api/v1/projects/{ids['project']}"
    r = client.post(f"{base}/shots/{ids['shot']}/generate", json={"prompt_override": "test"})
    assert r.status_code == 402


def test_provider_dimension_mismatch_is_visible(studio):
    client, _, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    job = post_ok(
        client,
        f"{base}/shots/{ids['shot']}/generate",
        {"prompt_override": "test", "provider_params": {"size": "1024x1024"}},
    )
    assert job["status"] == "succeeded"
    assert "1024x1024" in job["cost_raw"]["warnings"][0]
    assert "640x360" in job["cost_raw"]["warnings"][0]
    results = client.get(f"{base}/generations?target_type=shot&target_id={ids['shot']}").json()
    assert results[0]["input_refs"]["actual_media"] == {"width": 640, "height": 360}


def test_corrupt_provider_media_does_not_become_variant(studio, monkeypatch):
    client, sessions, ids = studio
    monkeypatch.setattr(
        MockProvider,
        "poll",
        lambda *_: GenerationResult(
            status="succeeded", outputs=[Output(type="image", data=b"<html>gateway failure</html>")]
        ),
    )
    base = f"/api/v1/projects/{ids['project']}"
    job = post_ok(client, f"{base}/shots/{ids['shot']}/generate", {"prompt_override": "test"})
    assert job["status"] == "failed" and "不能解码" in job["error"]
    assert client.get(f"{base}/generations?target_type=shot&target_id={ids['shot']}").json() == []


def test_queue_failure_is_retryable_and_viewer_cannot_mutate(studio, monkeypatch):
    client, sessions, ids = studio
    from app.tasks.generation_tasks import run_generation_job

    monkeypatch.setattr(settings, "celery_eager", False)
    monkeypatch.setattr(settings, "generation_executor", "celery")
    monkeypatch.setattr(
        run_generation_job,
        "apply_async",
        lambda **_: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    base = f"/api/v1/projects/{ids['project']}"
    failed = post_ok(client, f"{base}/shots/{ids['shot']}/generate", {"prompt_override": "test"})
    assert failed["status"] == "failed" and "队列" in failed["error"]
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert client.get(f"{base}/jobs/{failed['id']}").status_code == 200
    assert client.post(f"{base}/jobs/{failed['id']}/retry").status_code == 403
    assert client.post(f"{base}/jobs/{failed['id']}/cancel").status_code == 403


def test_local_recovery_does_not_touch_celery_jobs(studio):
    _, sessions, ids = studio
    with sessions() as db:
        local = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            request_type="image",
            status="running",
            cost_raw={"executor": "local"},
        )
        remote = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            request_type="image",
            status="running",
            cost_raw={"executor": "celery"},
        )
        db.add_all([local, remote])
        db.flush()
        local_id, remote_id = local.id, remote.id
        db.commit()
    jobs.recover_local()
    with sessions() as db:
        assert db.get(GenerationJob, local_id).status == "failed"
        assert db.get(GenerationJob, remote_id).status == "running"


def test_gateway_frame_and_parameter_mapping(monkeypatch):
    import json

    from app.adapters.contracts import GenerationRequest, ReferenceImage
    from app.adapters.generation.jimeng import JimengProvider

    requests = []

    def respond(req, **kwargs):
        requests.append(json.loads(req.data))
        return io.BytesIO(b'{"task_id":"gateway-job"}')

    monkeypatch.setattr("urllib.request.urlopen", respond)
    gateway = JimengProvider(
        endpoint="https://gateway.example.com",
        token="test-token",
        config={"features": ["first_frame", "last_frame"]},
    )
    gateway.submit(
        GenerationRequest(
            request_type="video",
            prompt="test",
            provider_params={"duration": 5, "aspect_ratio": "16:9"},
            references=[
                ReferenceImage(
                    blob_hash="one", role="first_frame", data_url="data:image/png;base64,AAAA"
                ),
                ReferenceImage(
                    blob_hash="two", role="last_frame", data_url="data:image/png;base64,BBBB"
                ),
            ],
        )
    )
    assert requests[0]["first_frame"].endswith("AAAA") and requests[0]["last_frame"].endswith(
        "BBBB"
    )
    assert requests[0]["duration"] == 5 and requests[0]["aspect_ratio"] == "16:9"
    assert "reference_images" not in requests[0]


def test_failed_job_retry_and_duplicate_execution(studio, monkeypatch):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    monkeypatch.setattr(
        MockProvider, "poll", lambda *_: (_ for _ in ()).throw(ValueError("Test provider failure"))
    )
    failed = post_ok(client, f"{base}/shots/{ids['shot']}/generate", {"prompt_override": "test"})
    assert failed["status"] == "failed" and "Test provider failure" in failed["error"]
    monkeypatch.setattr(
        MockProvider,
        "poll",
        lambda *_: GenerationResult(
            status="succeeded", outputs=[Output(type="image", data=media_engine.mock_image())]
        ),
    )
    retried = post_ok(client, f"{base}/jobs/{failed['id']}/retry")
    assert retried["status"] == "succeeded", retried
    assert retried["input_snapshot"]["retry_of"] == failed["id"]
    assert client.post(f"{base}/jobs/{failed['id']}/retry").status_code == 409
    with sessions() as db:
        jobs.execute(db, uuid.UUID(retried["id"]))
        rows = list(
            db.scalars(select(Generation).where(Generation.job_id == uuid.UUID(retried["id"])))
        )
        assert len(rows) == 1


def test_cancel_in_flight_discards_late_result(studio, monkeypatch):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    monkeypatch.setattr(settings, "celery_eager", False)
    monkeypatch.setattr(settings, "generation_executor", "local")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_worker = jobs._local

    def tracked(job_id):
        try:
            original_worker(job_id)
        finally:
            finished.set()

    monkeypatch.setattr(jobs, "_local", tracked)

    def slow(*_):
        entered.set()
        assert release.wait(10)
        return GenerationResult(
            status="succeeded", outputs=[Output(type="image", data=media_engine.mock_image())]
        )

    monkeypatch.setattr(MockProvider, "poll", slow)
    job = post_ok(client, f"{base}/shots/{ids['shot']}/generate", {"prompt_override": "test"})
    assert entered.wait(10)
    try:
        canceled = post_ok(client, f"{base}/jobs/{job['id']}/cancel")
        assert canceled["status"] == "canceled"
    finally:
        release.set()
    # Wait for this worker to finish before the fixture removes its schema.
    assert finished.wait(10)
    with sessions() as db:
        assert db.get(GenerationJob, uuid.UUID(job["id"])).status == "canceled"
        assert not list(
            db.scalars(select(Generation).where(Generation.job_id == uuid.UUID(job["id"])))
        )


def test_baseline_preserves_repeated_shot_timing(studio):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    timeline = post_ok(client, f"{base}/timelines", {"name": "Repeated clip baseline"})
    items = [
        {"shot_id": ids["shot"], "duration_ms": 1000},
        {"shot_id": ids["shot"], "in_point_ms": 2000, "duration_ms": 3000},
    ]
    assert (
        client.put(f"{base}/timelines/{timeline['id']}/items", json={"items": items}).status_code
        == 200
    )
    cut = post_ok(client, f"{base}/cuts", {"name": "Repeated cut", "timeline_id": timeline["id"]})
    baseline = post_ok(client, f"{base}/cuts/{cut['id']}/finalize")
    assert client.post(f"{base}/cuts/{cut['id']}/finalize").status_code == 409
    from app.models.versioning import BaselineItem, Version

    with sessions() as db:
        rows = list(
            db.scalars(
                select(BaselineItem).where(BaselineItem.baseline_id == uuid.UUID(baseline["id"]))
            )
        )
        assert len(rows) == 1
        snapshot = db.get(Version, rows[0].version_id).content
        assert [c["duration_ms"] for c in snapshot["timeline_items"]] == [1000, 3000]
