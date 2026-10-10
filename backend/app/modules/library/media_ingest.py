"""Decode declared library media through bounded, local-only container readers."""

import re
import warnings
from pathlib import Path

from PIL import Image, ImageOps

from app.modules.generation import media_engine
from app.modules.library.media_types import MAX_IMAGE_BYTES

MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_EDGE = 16384
MAX_DURATION_MS = 6 * 3600 * 1000
IMAGE_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def check_canceled(canceled):
    if canceled():
        raise media_engine.Canceled()


def read_image(path, canceled=lambda: False):
    check_canceled(canceled)
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("图片超过 64 MiB，请重新上传")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(path) as source:
                if source.format not in IMAGE_MIME:
                    raise ValueError("仅支持 JPEG、PNG、WebP 静态图片，请重新上传")
                if (
                    source.width * source.height > MAX_IMAGE_PIXELS
                    or max(source.size) > MAX_IMAGE_EDGE
                ):
                    raise ValueError("图片像素尺寸超过限制，请重新上传")
                if getattr(source, "is_animated", False) or getattr(source, "n_frames", 1) != 1:
                    raise ValueError("素材图片必须为静态图片，请重新上传")
                mime = IMAGE_MIME[source.format]
                source.verify()
            with Image.open(path) as source:
                source.load()  # Validate compressed pixels, not just the file header.
                has_alpha = "A" in source.getbands() or "transparency" in source.info
                image = ImageOps.exif_transpose(source).convert("RGBA" if has_alpha else "RGB")
        except (
            OSError,
            SyntaxError,
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ) as exc:
            raise ValueError("图片不能完整解码，请重新上传") from exc
    check_canceled(canceled)
    return image, mime


def container(path, media_type):
    with path.open("rb") as source:
        header = source.read(4096)
    if media_type == "video" and header.startswith(b"\x1a\x45\xdf\xa3"):
        # EBML's DocType determines whether this is WebM or Matroska.
        webm = b"\x42\x82\x84webm" in header
        return "matroska", "video/webm" if webm else "video/x-matroska"
    if header[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free"):
        if media_type == "audio":
            return "mov", "audio/mp4"
        return "mov", "video/quicktime" if header[8:12] == b"qt  " else "video/mp4"
    if media_type == "audio":
        if header[:4] in (b"RIFF", b"RF64") and header[8:12] == b"WAVE":
            return "wav", "audio/wav"
        if header.startswith(b"fLaC"):
            return "flac", "audio/flac"
        if header.startswith(b"OggS"):
            return "ogg", "audio/ogg"
        if header.startswith(b"ID3") or (
            len(header) > 1 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
        ):
            return "mp3", "audio/mpeg"
    # Never let FFmpeg auto-select network playlists or arbitrary image demuxers.
    raise ValueError("文件实际类型或容器与所选素材类型不符，请重新上传")


def input_options(info):
    return [
        "-protocol_whitelist",
        "file,pipe",
        "-max_pixels",
        str(MAX_IMAGE_PIXELS),
        "-threads",
        "2",
        "-f",
        info["input_format"],
        *(
            ["-enable_drefs", "0", "-use_absolute_path", "0"]
            if info["input_format"] == "mov"
            else []
        ),
    ]


def probe_media(path, media_type, canceled=lambda: False, *, max_duration_ms=MAX_DURATION_MS):
    """Report real stream types; attached cover artwork never counts as video."""
    path = Path(path)
    if media_type not in ("video", "image", "audio"):
        raise ValueError("未知素材类型，请重新上传")
    if media_type == "image":
        image, mime = read_image(path, canceled)
        try:
            return {
                "media_type": "image",
                "mime": mime,
                "input_format": None,
                "duration_ms": None,
                "width": image.width,
                "height": image.height,
                "has_video": False,
                "has_audio": False,
            }
        finally:
            image.close()
    input_format, mime = container(path, media_type)
    info = {"input_format": input_format, "mime": mime, "media_type": media_type}
    log = media_engine.run(
        [
            "-xerror",
            *input_options(info),
            "-i",
            str(path),
            "-map",
            "0:V:0?",
            "-map",
            "0:a:0?",
            "-t",
            "0.05",
            "-f",
            "null",
            "-",
        ],
        canceled,
        timeout=30,
    ).split("Output #", 1)[0]
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", log)
    if not duration:
        raise ValueError("素材没有可读取的时长，请重新上传")
    hour, minute, second = map(float, duration.groups())
    duration_ms = round((hour * 3600 + minute * 60 + second) * 1000)
    if not 0 < duration_ms <= max_duration_ms:
        raise ValueError("素材时长超过限制或无有效内容，请重新上传")
    videos = [
        line
        for line in log.splitlines()
        if "Stream #" in line and "Video:" in line and "attached pic" not in line.lower()
    ]
    has_audio = any("Stream #" in line and "Audio:" in line for line in log.splitlines())
    if media_type == "audio" and (not has_audio or videos):
        raise ValueError("请选择只有音轨的音频文件，请重新上传")
    if media_type == "video" and not videos:
        raise ValueError("素材没有真实视频流，封面图片不能作为视频，请重新上传")
    width = height = None
    if videos:
        # MP4 stream IDs contain e.g. [0x1]; they are not frame dimensions.
        dimensions = re.search(r"\b([1-9]\d{0,4})x([1-9]\d{0,4})\b", videos[0])
        if dimensions is None:
            raise ValueError("无法读取视频分辨率，请重新上传")
        width, height = map(int, dimensions.groups())
        if (
            not width
            or not height
            or width * height > MAX_IMAGE_PIXELS
            or max(width, height) > 8192
        ):
            raise ValueError("视频分辨率超过限制，请重新上传")
    return {
        **info,
        "duration_ms": duration_ms,
        "width": width,
        "height": height,
        "has_video": bool(videos),
        "has_audio": has_audio,
    }


def build_preview(source, folder, info, canceled=lambda: False):
    """Return safe preview files and metadata; original bytes are never edited."""
    kind = info["media_type"]
    if kind == "image":
        image, _ = read_image(source, canceled)
        try:
            image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
            image.info.clear()  # Preview never inherits EXIF, text or embedded profiles.
            alpha = "A" in image.getbands()
            path = folder / ("preview.png" if alpha else "preview.jpg")
            image.save(path, "PNG" if alpha else "JPEG", **({} if alpha else {"quality": 90}))
            check_canceled(canceled)
            item = (
                path,
                "image/png" if alpha else "image/jpeg",
                {"width": image.width, "height": image.height},
            )
            return {"proxy": item, "poster": item}
        finally:
            image.close()
    audio = kind == "audio"
    proxy = folder / ("proxy.m4a" if audio else "proxy.mp4")
    args = ["-xerror", "-err_detect", "explode", *input_options(info), "-i", str(source)]
    if audio:
        args += ["-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000"]
    else:
        args += [
            "-map",
            "0:V:0",
            "-map",
            "0:a:0?",
            "-vf",
            "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
        ]
    media_engine.run(
        [
            *args,
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-threads",
            "2",
            "-movflags",
            "+faststart",
            str(proxy),
        ],
        canceled=canceled,
        timeout=6 * 3600,
    )
    actual = probe_media(proxy, kind, canceled)
    if abs(actual["duration_ms"] - info["duration_ms"]) > 1500:
        raise ValueError("预览时长与原片不一致，请重新上传完整素材")
    metadata = {key: actual[key] for key in ("duration_ms", "width", "height")}
    result = {"proxy": (proxy, "audio/mp4" if audio else "video/mp4", metadata), "poster": None}
    if not audio:
        poster = folder / "poster.jpg"
        media_engine.run(
            [
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(proxy),
                "-map",
                "0:V:0",
                "-frames:v",
                "1",
                "-update",
                "1",
                str(poster),
            ],
            canceled=canceled,
            timeout=30,
        )
        image, _ = read_image(poster, canceled)
        result["poster"] = (poster, "image/jpeg", {"width": image.width, "height": image.height})
        image.close()
    check_canceled(canceled)
    return result
