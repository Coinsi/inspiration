"""Supported library formats; ingestion verifies content independently of extensions."""

from pathlib import Path

from app.core.errors import CapabilityUnsupported

EXTENSIONS = {
    "video": {".mp4", ".mov", ".mkv", ".webm"},
    "image": {".jpg", ".jpeg", ".png", ".webp"},
    "audio": {".mp3", ".wav", ".m4a", ".flac", ".ogg"},
}
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def for_filename(filename):
    suffix = Path(filename).suffix.lower()
    for kind, formats in EXTENSIONS.items():
        if suffix in formats:
            return kind
    raise CapabilityUnsupported("请选择支持的视频、JPEG/PNG/WebP 图片或 MP3/WAV/M4A/FLAC/OGG 音频")
