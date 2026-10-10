"""Real bounded image/audio/video decoding; fixtures contain no user media."""

import hashlib

import pytest
from PIL import Image

from app.modules.generation import media_engine
from app.modules.library import media_ingest


def tone(path):
    media_engine.run(["-f", "lavfi", "-i", "sine=frequency=660:duration=0.8", str(path)])
    return path


@pytest.mark.parametrize("suffix,fmt", [("jpg", "JPEG"), ("png", "PNG"), ("webp", "WEBP")])
def test_static_image_preview_is_decoded_and_source_preserved(tmp_path, suffix, fmt):
    source = tmp_path / f"input.{suffix}"
    Image.new("RGB", (96, 64), "navy").save(source, fmt)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    info = media_ingest.probe_media(source, "image")
    output = media_ingest.build_preview(source, tmp_path, info)
    assert info["duration_ms"] is None and (info["width"], info["height"]) == (96, 64)
    assert not info["has_video"] and not info["has_audio"]
    assert output["proxy"] == output["poster"]
    with Image.open(output["proxy"][0]) as preview:
        preview.load()
        assert preview.size == (96, 64) and preview.format == "JPEG"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_image_orientation_and_alpha_are_normalized_without_metadata(tmp_path):
    source = tmp_path / "oriented.png"
    image = Image.new("RGBA", (40, 20), (30, 60, 90, 128))
    exif = image.getexif()
    exif[274] = 6
    image.save(source, exif=exif)
    info = media_ingest.probe_media(source, "image")
    result = media_ingest.build_preview(source, tmp_path, info)
    assert (info["width"], info["height"]) == (20, 40)
    assert result["proxy"][1] == "image/png"
    with Image.open(result["proxy"][0]) as preview:
        assert preview.size == (20, 40)
        assert preview.getpixel((5, 5))[3] == 128
        assert not preview.getexif()


def test_animated_truncated_and_oversized_images_are_rejected(tmp_path, monkeypatch):
    source = tmp_path / "animated.png"
    Image.new("RGB", (20, 20), "red").save(
        source, save_all=True, append_images=[Image.new("RGB", (20, 20), "blue")], duration=100
    )
    with pytest.raises(ValueError, match="静态"):
        media_ingest.probe_media(source, "image")
    Image.new("RGB", (20, 20), "red").save(source)
    monkeypatch.setattr(media_ingest, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="像素"):
        media_ingest.probe_media(source, "image")
    monkeypatch.setattr(media_ingest, "MAX_IMAGE_PIXELS", 40_000_000)
    monkeypatch.setattr(media_ingest, "MAX_IMAGE_BYTES", source.stat().st_size - 1)
    with pytest.raises(ValueError, match="64 MiB"):
        media_ingest.probe_media(source, "image")
    monkeypatch.setattr(media_ingest, "MAX_IMAGE_BYTES", 64 * 1024 * 1024)
    source.write_bytes(source.read_bytes()[:45])
    with pytest.raises(ValueError, match="解码"):
        media_ingest.probe_media(source, "image")


@pytest.mark.parametrize(
    "suffix,mime",
    [
        ("wav", "audio/wav"),
        ("mp3", "audio/mpeg"),
        ("m4a", "audio/mp4"),
        ("flac", "audio/flac"),
        ("ogg", "audio/ogg"),
    ],
)
def test_audio_formats_have_a_real_audio_preview_and_no_fake_video(tmp_path, suffix, mime):
    source = tone(tmp_path / f"source.{suffix}")
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    info = media_ingest.probe_media(source, "audio")
    assert info["mime"] == mime and info["has_audio"] and not info["has_video"]
    assert info["width"] is None and info["height"] is None
    assert 700 <= info["duration_ms"] <= 1000
    result = media_ingest.build_preview(source, tmp_path, info)
    assert result["poster"] is None and result["proxy"][1] == "audio/mp4"
    actual = media_ingest.probe_media(result["proxy"][0], "audio")
    assert actual["has_audio"] and not actual["has_video"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_attached_cover_does_not_turn_audio_into_video(tmp_path):
    audio = tone(tmp_path / "tone.wav")
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (64, 64), "blue").save(cover)
    source = tmp_path / "with-cover.m4a"
    media_engine.run(
        [
            "-i",
            str(audio),
            "-i",
            str(cover),
            "-map",
            "0:a:0",
            "-map",
            "1:v:0",
            "-c:a",
            "aac",
            "-c:v",
            "copy",
            "-disposition:v:0",
            "attached_pic",
            str(source),
        ]
    )
    info = media_ingest.probe_media(source, "audio")
    assert info["has_audio"] and not info["has_video"] and info["width"] is None
    with pytest.raises(ValueError, match="封面"):
        media_ingest.probe_media(source, "video")


def test_container_spoofing_and_playlists_are_rejected_before_external_reads(tmp_path):
    source = tmp_path / "fake.mp4"
    Image.new("RGB", (64, 64), "blue").save(source, "PNG")
    with pytest.raises(ValueError, match="实际类型"):
        media_ingest.probe_media(source, "video")
    source.write_text("#EXTM3U\nhttp://127.0.0.1:9/private\n", encoding="utf-8")
    with pytest.raises(ValueError, match="实际类型"):
        media_ingest.probe_media(source, "audio")
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x64:r=10:d=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=0.5",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ]
    )
    with pytest.raises(ValueError, match="只有音轨"):
        media_ingest.probe_media(source, "audio")


def test_duration_and_cancellation_limits_are_enforced(tmp_path):
    source = tone(tmp_path / "tone.wav")
    with pytest.raises(ValueError, match="时长"):
        media_ingest.probe_media(source, "audio", max_duration_ms=100)
    with pytest.raises(media_engine.Canceled):
        media_ingest.probe_media(source, "audio", lambda: True)


@pytest.mark.parametrize(
    "suffix,mime",
    [
        ("mp4", "video/mp4"),
        ("mov", "video/quicktime"),
        ("mkv", "video/x-matroska"),
        ("webm", "video/webm"),
    ],
)
def test_existing_video_containers_remain_supported(tmp_path, suffix, mime):
    source = tmp_path / f"source.{suffix}"
    codec = ["-c:v", "libvpx", "-deadline", "realtime"] if suffix == "webm" else ["-c:v", "libx264"]
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=80x48:r=10:d=0.5",
            *codec,
            str(source),
        ]
    )
    info = media_ingest.probe_media(source, "video")
    assert info["mime"] == mime and info["has_video"]
    assert (info["width"], info["height"]) == (80, 48)
    result = media_ingest.build_preview(source, tmp_path, info)
    assert result["proxy"][1] == "video/mp4" and result["poster"][1] == "image/jpeg"
