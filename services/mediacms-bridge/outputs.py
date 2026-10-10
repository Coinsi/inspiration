"""Resolve only server-selected output files; never accepts a caller's path."""

import mimetypes
from pathlib import Path


def safe_media_path(value, media_root):
    if not value:
        return None
    root = Path(media_root).resolve()
    path = Path(value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def output_item(token, key, value, media_root, **extra):
    path = safe_media_path(value, media_root)
    if path is None:
        return None
    item = {
        "key": key,
        "url": f"/api/inspiration/v1/media/{token}/files/{key}",
        "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size_bytes": path.stat().st_size,
        **extra,
    }
    return item, path
