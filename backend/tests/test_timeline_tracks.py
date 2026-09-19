"""Actual mixing/caption encoding plus isolated PostgreSQL revision and permission checks."""
# ruff: noqa: F811

import io
import math
import struct
import uuid
import wave

import pytest
from PIL import Image, ImageChops
from sqlalchemy import select

from app.models.generation import GenerationJob
from app.modules.generation import media_engine
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def tone(seconds=3, frequency=440):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(
            b"".join(
                struct.pack("<h", round(12000 * math.sin(2 * math.pi * frequency * n / 48000)))
                for n in range(round(seconds * 48000))
            )
        )
    return buffer.getvalue()


def track(hash="tone", **overrides):
    return {
        "blob_hash": hash,
        "start_ms": 500,
        "in_point_ms": 0,
        "duration_ms": 1500,
        "gain_db": -6,
        "fade_in_ms": 200,
        "fade_out_ms": 200,
        "muted": False,
        **overrides,
    }


@pytest.mark.parametrize("kind", ["dissolve", "fadeblack", "wipeleft"])
def test_real_transition_duration_and_picture_blend(tmp_path, kind):
    from app.modules.media.timing import duration

    blobs = {}
    for color in ("red", "blue"):
        b = io.BytesIO()
        Image.new("RGB", (320, 180), color).save(b, "PNG")
        blobs[color] = b.getvalue()
    clips = [
        {"blob_hash": color, "output_type": "image", "duration_ms": 1500}
        for color in ("red", "blue")
    ]
    clips[0]["transition"] = {"type": kind, "duration_ms": 500}
    assert duration(clips) == 2500
    out = tmp_path / "transition.mp4"
    out.write_bytes(
        media_engine.render(clips, blobs.__getitem__, {"height": 720, "aspect_ratio": "16:9"})
    )
    log = media_engine.run(["-i", str(out), "-f", "null", "-"])
    assert "00:00:02.50" in log
    frame = tmp_path / "blend.png"
    media_engine.run(["-ss", "1.25", "-i", str(out), "-frames:v", "1", str(frame)])
    im = Image.open(frame)
    r, g, b = im.getpixel((im.width // 2, im.height // 2))[:3]
    if kind == "dissolve":
        assert 80 < r < 180 and 80 < b < 180 and g < 10
    elif kind == "fadeblack":
        assert max(r, g, b) < 90  # Allow one 24-fps sample either side of the darkest instant.
    else:
        assert im.getpixel((100, im.height // 2))[0] > 200
        assert im.getpixel((im.width - 100, im.height // 2))[2] > 200
    clips[0]["transition"]["duration_ms"] = 800
    with pytest.raises(ValueError, match="一半"):
        duration(clips)


def test_real_audio_placement_gain_mute_and_captions(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (640, 360), "black").save(buf, "PNG")
    blobs = {"image": buf.getvalue(), "tone": tone()}
    clips = [{"blob_hash": "image", "output_type": "image", "duration_ms": 2500}]
    cues = [{"start_ms": 300, "end_ms": 1900, "text": "真实字幕 Test caption"}]
    options = {"height": 720, "aspect_ratio": "16:9", "audio": [track()], "subtitles": cues}
    output = tmp_path / "mixed.mp4"
    output.write_bytes(media_engine.render(clips, blobs.__getitem__, options))
    wav = tmp_path / "decoded.wav"
    media_engine.run(["-i", str(output), "-vn", "-c:a", "pcm_s16le", "-ac", "1", str(wav)])
    with wave.open(str(wav), "rb") as f:
        rate = f.getframerate()
        samples = struct.unpack("<" + "h" * f.getnframes(), f.readframes(f.getnframes()))

    def rms(start, end):
        values = samples[round(start * rate) : round(end * rate)]
        return math.sqrt(sum(x * x for x in values) / len(values))

    assert rms(0.1, 0.3) < 10
    assert 2700 < rms(0.9, 1.2) < 3300  # Mono-to-stereo equal-power pan and -6 dB, decoded to mono.
    assert rms(2.15, 2.3) < 10
    before, caption = tmp_path / "before.png", tmp_path / "caption.png"
    for ms, dest in [(100, before), (1000, caption)]:
        media_engine.run(["-ss", str(ms / 1000), "-i", str(output), "-frames:v", "1", str(dest)])
    assert ImageChops.difference(Image.open(before), Image.open(caption)).getbbox() is not None
    soft = tmp_path / "soft.mp4"
    soft.write_bytes(
        media_engine.render(
            clips, blobs.__getitem__, {**options, "subtitle_mode": "track", "mute": True}
        )
    )
    decoded = tmp_path / "captions.srt"
    media_engine.run(["-i", str(soft), "-map", "0:s:0", str(decoded)])
    assert "真实字幕 Test caption" in decoded.read_text(encoding="utf-8")
    muted = tmp_path / "muted.wav"
    media_engine.run(["-i", str(soft), "-vn", "-c:a", "pcm_s16le", str(muted)])
    with wave.open(str(muted), "rb") as f:
        values = f.readframes(f.getnframes())
        assert max(abs(x[0]) for x in struct.iter_unpack("<h", values)) < 10
    with pytest.raises(media_engine.Canceled):
        media_engine.render(clips, blobs.__getitem__, options, canceled=lambda: True)


def test_document_history_subtitles_audio_isolation_and_render_snapshot(studio, monkeypatch):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    t = post_ok(client, base + "/timelines", {"name": "Tracks"})
    path = f"{base}/timelines/{t['id']}"
    doc = client.get(path + "/document").json()
    assert doc == {"revision": 0, "items": [], "audio": [], "subtitles": [], "visuals": []}
    uploaded = client.post(path + "/audio", files={"file": ("music.wav", tone(), "audio/wav")})
    assert uploaded.status_code == 200, uploaded.text
    sound = uploaded.json()
    assert sound["duration_ms"] == 3000
    assert len(client.get(base + "/audio-sources").json()["items"]) == 1
    assert (
        client.post(
            path + "/audio", files={"file": ("invalid.wav", b"broken", "audio/wav")}
        ).status_code
        == 422
    )
    parsed = post_ok(
        client,
        path + "/subtitles/parse",
        {"content": "1\n00:00:00,200 --> 00:00:01,500\n一句字幕\n"},
    )
    items = [{"shot_id": ids["shot"], "duration_ms": 3000}]
    a = {
        "generation_id": sound["id"],
        "name": "First",
        "duration_ms": 2000,
        "start_ms": 500,
        "gain_db": -6,
    }
    data = {
        **doc,
        "items": items,
        "audio": [a, {**a, "name": "Second", "gain_db": -12}],
        "subtitles": parsed["items"],
    }
    saved = client.put(path + "/document", json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert client.put(path + "/document", json=data).status_code == 409
    assert client.get(path + "/document").json()["audio"][0]["gain_db"] == -6
    assert [a["name"] for a in client.get(path + "/document").json()["audio"]] == [
        "First",
        "Second",
    ]
    assert "一句字幕" in client.get(path + "/subtitles.srt").text
    bad = {**saved.json(), "audio": [{**a, "in_point_ms": 2500}]}
    assert client.put(path + "/document", json=bad).status_code == 422
    assert client.get(path + "/document").json()["revision"] == 1
    assert (
        client.put(path + "/items", json={"items": [{**items[0], "duration_ms": 500}]}).status_code
        == 422
    )
    history = client.get(path + "/history").json()
    assert [r["revision"] for r in history["items"]] == [1, 0]
    restored = post_ok(client, path + "/history/0/restore", {"revision": 1})
    assert restored["revision"] == 2 and restored["items"] == [] and restored["audio"] == []
    assert client.post(path + "/history/1/restore", json={"revision": 1}).status_code == 409
    restored = post_ok(client, path + "/history/1/restore", {"revision": 2})
    assert restored["revision"] == 3 and restored["subtitles"] == parsed["items"]
    assert client.get(f"{base}/timelines/{uuid.uuid4()}/history").status_code == 404
    assert client.get(
        f"/api/v1/projects/{ids['other']}/timelines/{t['id']}/document"
    ).status_code in (403, 404)
    # Submit must snapshot the saved track state, independent of subsequent editing.
    g = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("frame.png", media_engine.mock_image(), "image/png")},
    ).json()
    restored["items"][0]["generation_id"] = g["id"]
    doc = client.put(path + "/document", json=restored).json()
    from app.modules.generation import jobs

    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    job = post_ok(client, path + "/render", {"revision": doc["revision"], "subtitle_mode": "track"})
    assert client.post(path + "/render", json={"revision": 0}).status_code == 409
    with sessions() as db:
        row = db.scalar(select(GenerationJob).where(GenerationJob.id == uuid.UUID(job["id"])))
        snapshot = row.input_snapshot
        # Local job nests its payload under the operation-specific key.
        assert "audio" in str(snapshot) and sound["blob_hash"] in str(snapshot)
    cut = post_ok(client, base + "/cuts", {"name": "With captions", "timeline_id": t["id"]})
    frozen = post_ok(client, base + f"/cuts/{cut['id']}/finalize")
    from app.models.versioning import BaselineItem, Version

    with sessions() as db:
        item = db.scalar(
            select(BaselineItem).where(
                BaselineItem.baseline_id == uuid.UUID(frozen["id"]),
                BaselineItem.entity_type == "timeline",
            )
        )
        assert db.get(Version, item.version_id).content["audio"][0]["generation_id"] == sound["id"]
