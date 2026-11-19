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


def test_picture_gaps_survive_an_actual_video_export(tmp_path):
    source = tmp_path / "source.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=red:s=160x90:r=24:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ]
    )
    clips = [
        {
            "output_type": "video",
            "blob_hash": "video",
            "in_point_ms": 250,
            "duration_ms": 1000,
            "gap_before_ms": 500,
            "gap_after_ms": 500,
        }
    ]
    with media_engine.render_file(clips, lambda _: source, {"height": 180}) as movie:
        for ms, is_black in ((250, True), (1000, False), (1750, True)):
            frame = tmp_path / f"{ms}.png"
            media_engine.run(
                ["-ss", str(ms / 1000), "-i", str(movie), "-frames:v", "1", str(frame)]
            )
            with Image.open(frame) as image:
                red = image.getpixel((80, 45))[0]
                assert red < 10 if is_black else red > 200
        wav = tmp_path / "with-gaps.wav"
        media_engine.run(["-i", str(movie), "-vn", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
        with wave.open(str(wav), "rb") as sound:
            for second, audible in ((0.2, False), (0.9, True), (1.7, False)):
                sound.setpos(round(second * sound.getframerate()))
                samples = struct.iter_unpack("<h", sound.readframes(sound.getframerate() // 10))
                peak = max(abs(value) for (value,) in samples)
                assert peak > 100 if audible else peak < 10


def test_older_clients_preserve_gaps_and_older_revisions_restore_them(studio):
    from app.models.timeline import TimelineRevision

    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}/timelines"
    timeline = post_ok(client, base, {"name": "Picture gaps"})
    path = f"{base}/{timeline['id']}"
    old = {"shot_id": ids["shot"], "duration_ms": 1000}
    first = client.put(path + "/items", json={"items": [old]})
    assert first.status_code == 200
    doc = client.get(path + "/document").json()
    # Match the stored format used by projects created before this migration.
    with sessions.begin() as db:
        row = db.scalar(
            select(TimelineRevision).where(
                TimelineRevision.timeline_id == uuid.UUID(timeline["id"]),
                TimelineRevision.revision == doc["revision"],
            )
        )
        row.snapshot = {
            **row.snapshot,
            "items": [
                {k: v for k, v in item.items() if not k.startswith("gap_")}
                for item in row.snapshot["items"]
            ],
        }
    doc["items"][0].update(gap_before_ms=200, gap_after_ms=400)
    assert client.put(path + "/document", json=doc).status_code == 200
    assert client.put(path + "/items", json={"items": [old]}).status_code == 200
    kept = client.get(path + "/document").json()
    assert (kept["items"][0]["gap_before_ms"], kept["items"][0]["gap_after_ms"]) == (200, 400)
    old_doc = post_ok(
        client, path + f"/history/{doc['revision']}/restore", {"revision": kept["revision"]}
    )
    assert (old_doc["items"][0]["gap_before_ms"], old_doc["items"][0]["gap_after_ms"]) == (0, 0)


def test_chat_quotes_the_selected_subtitle_and_picture_from_the_saved_revision(studio):
    client, _, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    timeline = post_ok(client, base + "/timelines", {"name": "Quoted edit"})
    path = f"{base}/timelines/{timeline['id']}/document"
    cue = {"id": str(uuid.uuid4()), "text": "原来的字幕", "start_ms": 750, "end_ms": 1250}
    clip = {"shot_id": ids["shot"], "duration_ms": 2000, "in_point_ms": 100, "gap_before_ms": 250}
    saved = client.put(path, json={"revision": 0, "items": [clip], "audio": [], "subtitles": [cue]})
    assert saved.is_success, saved.text
    changed = saved.json()
    changed["subtitles"][0]["text"] = "新字幕"
    assert client.put(path, json=changed).is_success
    selection = {
        "timeline_id": timeline["id"],
        "revision": saved.json()["revision"],
        "track": "subtitles",
        "clip_id": cue["id"],
        "start_ms": 800,
        "end_ms": 1200,
    }
    chat = post_ok(client, base + "/conversations", {})
    messages = f"{base}/conversations/{chat['id']}/messages"
    payload = {
        "request_key": str(uuid.uuid4()),
        "content": "调整这句字幕",
        "mode": "test",
        "cut_selection": selection,
    }
    response = post_ok(client, messages, payload)
    quote = next(m for m in response["messages"] if m["role"] == "user")["context"]["cut_selection"]
    assert quote["focus"]["text"] == "原来的字幕"
    assert (quote["clips"][0]["source_start_ms"], quote["clips"][0]["source_end_ms"]) == (650, 1050)
    invalid = {
        **payload,
        "request_key": str(uuid.uuid4()),
        "cut_selection": {**selection, "clip_id": str(uuid.uuid4())},
    }
    assert client.post(messages, json=invalid).status_code == 422


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
    assert restored["revision"] == 3
    assert [{k: v for k, v in cue.items() if k != "id"} for cue in restored["subtitles"]] == parsed["items"]
    assert all(cue["id"] for cue in restored["subtitles"])
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
