"""Real visual composition and isolated document/source/history contracts."""

# ruff: noqa: F811
import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import select

from app.models.generation import Generation, GenerationJob
from app.modules.generation import jobs, media_engine
from app.modules.timeline.schemas import VisualIn
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def layer(**changes):
    return {
        "id": str(uuid.uuid4()),
        "generation_id": str(uuid.uuid4()),
        "name": "Overlay",
        "track": 1,
        "start_ms": 500,
        "in_point_ms": 0,
        "duration_ms": 1000,
        "x": 0.5,
        "y": 0,
        "width": 0.5,
        "height": 1,
        "opacity": 1,
        "fit": "cover",
        "hidden": False,
        **changes,
    }


@pytest.mark.parametrize("fit", ["cover", "contain"])
def test_actual_layers_time_opacity_order_alpha_video_audio(tmp_path, fit):
    blobs = {}
    for name, color in [("base", "red"), ("square", "blue"), ("top", (0, 255, 0, 128))]:
        b = io.BytesIO()
        Image.new("RGBA", (100, 100), color).save(b, "PNG")
        blobs[name] = b.getvalue()
    video = tmp_path / "video.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=yellow:s=100x100:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=100x100:r=24:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ]
    )
    blobs["video"] = video.read_bytes()
    layers = [
        layer(blob_hash="video", output_type="video", in_point_ms=1000, fit=fit),
        layer(
            blob_hash="top",
            output_type="image",
            track=3,
            start_ms=750,
            duration_ms=250,
            x=0.6,
            y=0.4,
            width=0.1,
            height=0.2,
            opacity=0.5,
        ),
        layer(blob_hash="square", output_type="image", track=2, hidden=True, x=0, width=1),
    ]
    out = tmp_path / "result.mp4"
    out.write_bytes(
        media_engine.render(
            [{"blob_hash": "base", "output_type": "image", "duration_ms": 2000}],
            blobs.__getitem__,
            {"height": 720, "aspect_ratio": "1:1", "visuals": layers},
        )
    )
    log = media_engine.run(["-i", str(out), "-f", "null", "-"])
    assert "00:00:02.00" in log and "Audio:" in log

    def pixel(t, x, y):
        frame = tmp_path / f"frame-{t}.png"
        media_engine.run(["-ss", str(t), "-i", str(out), "-frames:v", "1", str(frame)])
        return Image.open(frame).getpixel((x, y))[:3]

    assert pixel(0.2, 500, 360)[0] > 240
    assert pixel(1.7, 500, 360)[0] > 240  # Gone after end, no frozen overlay.
    assert pixel(1.2, 500, 360)[2] > 240  # Video in-point chose blue, not yellow.
    r, g, b = pixel(0.85, 460, 360)
    assert r < 15 and 45 < g < 85 and 170 < b < 215  # Alpha .5 * opacity .5 on top of blue.
    top = pixel(1.2, 500, 30)
    assert top[0 if fit == "contain" else 2] > 240  # Contain padding stays transparent.


def test_visual_document_isolation_history_legacy_and_snapshot(studio, monkeypatch):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    t = post_ok(client, base + "/timelines", {"name": "Visual tracks"})
    path = f"{base}/timelines/{t['id']}"
    g = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("frame.png", media_engine.mock_image(), "image/png")},
    ).json()
    v = layer(generation_id=g["id"])
    data = {
        "revision": 0,
        "items": [{"shot_id": ids["shot"], "generation_id": g["id"], "duration_ms": 2000}],
        "visuals": [v],
    }
    saved = client.put(path + "/document", json=data)
    assert saved.status_code == 200, saved.text
    doc = saved.json()
    assert doc["visuals"][0]["generation_id"] == g["id"]
    assert client.get(f"{base}/generations/{g['id']}").status_code == 200
    assert client.get(f"/api/v1/projects/{ids['other']}/generations/{g['id']}").status_code in (
        403,
        404,
    )
    for patch in [{"start_ms": 1500}, {"width": 0.8}, {"generation_id": str(uuid.uuid4())}]:
        bad = client.put(path + "/document", json={**doc, "visuals": [{**v, **patch}]})
        assert bad.status_code in (422, 404), bad.text
        assert client.get(path + "/document").json()["revision"] == 1
    assert (
        client.put(
            path + "/document", json={**doc, "visuals": [v, layer(generation_id=g["id"])]}
        ).status_code
        == 422
    )
    assert client.put(path + "/document", json={**doc, "visuals": [v, v]}).status_code == 422
    # Invalid cross-project source cannot be smuggled through the new read/overlay APIs.
    with sessions() as db:
        foreign = Generation(
            job_id=uuid.UUID(g["job_id"]),
            project_id=uuid.UUID(str(ids["other"])),
            target_type="shot",
            target_id=uuid.UUID(str(ids["shot"])),
            provider="upload",
            model="upload",
            output_type="image",
            output_blob_hash=g["output_blob_hash"],
        )
        db.add(foreign)
        db.commit()
        foreign_id = str(foreign.id)
    assert (
        client.put(
            path + "/document", json={**doc, "visuals": [{**v, "generation_id": foreign_id}]}
        ).status_code
        == 404
    )
    # Item-only legacy operations preserve layers, and cannot shorten under them.
    assert client.put(path + "/items", json={"items": data["items"]}).status_code == 200
    doc = client.get(path + "/document").json()
    assert len(doc["visuals"]) == 1
    assert (
        client.put(
            path + "/items", json={"items": [{**data["items"][0], "duration_ms": 500}]}
        ).status_code
        == 422
    )
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    job = post_ok(client, path + "/render", {"revision": doc["revision"]})
    cleared = client.put(path + "/document", json={**doc, "visuals": []}).json()
    with sessions() as db:
        snapshot = db.scalar(
            select(GenerationJob).where(GenerationJob.id == uuid.UUID(job["id"]))
        ).input_snapshot
        assert g["output_blob_hash"] in str(snapshot) and v["id"] in str(snapshot)
    restored = post_ok(client, path + "/history/1/restore", {"revision": cleared["revision"]})
    assert restored["visuals"] == doc["visuals"]
    restored = post_ok(client, path + "/history/0/restore", {"revision": restored["revision"]})
    assert restored["visuals"] == []


def test_visual_finite_and_bounds():
    for patch in [
        {"x": float("nan")},
        {"opacity": float("inf")},
        {"x": 0.8},
        {"track": 4},
        {"width": 0},
    ]:
        with pytest.raises(ValueError):
            VisualIn.model_validate(layer(**patch))


def test_legacy_video_duration_is_probed_and_invalid_save_is_atomic(studio, tmp_path):
    from app.models.storage import Blob

    client, sessions, ids = studio
    source = tmp_path / "old-video.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=100x100:r=24:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ]
    )
    base = f"/api/v1/projects/{ids['project']}"
    response = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("old.mp4", source.read_bytes(), "video/mp4")},
    )
    assert response.status_code == 200, response.text
    g = response.json()
    with sessions() as db:
        db.get(Blob, g["output_blob_hash"]).duration_ms = None
        db.commit()
    t = post_ok(client, base + "/timelines", {"name": "Legacy video"})
    path = f"{base}/timelines/{t['id']}/document"
    data = {
        "revision": 0,
        "items": [{"shot_id": ids["shot"], "duration_ms": 3000}],
        "visuals": [layer(generation_id=g["id"], in_point_ms=1500)],
    }
    assert client.put(path, json=data).status_code == 422
    assert client.get(path).json()["revision"] == 0
    data["visuals"][0]["in_point_ms"] = 1000
    assert client.put(path, json=data).status_code == 200
    with sessions() as db:
        assert db.get(Blob, g["output_blob_hash"]).duration_ms == 2000
