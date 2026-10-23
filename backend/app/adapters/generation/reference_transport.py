"""Reference delivery belongs to the provider adapter, never to the agent."""

import base64

from app.core.errors import CapabilityUnsupported

IMAGE_MIMES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_IMAGE_BYTES = 50 * 1024 * 1024


def image_file(ref):
    if ref.kind != "image" or not ref.data_url:
        raise CapabilityUnsupported("请选择已读取的参考图片")
    try:
        header, encoded = ref.data_url.split(",", 1)
        mime = header.removeprefix("data:").removesuffix(";base64")
        if header != f"data:{mime};base64" or mime not in IMAGE_MIMES:
            raise ValueError()
        if len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
            raise ValueError()
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError()
    except (ValueError, TypeError):
        raise CapabilityUnsupported(
            "参考图片必须为有效的 PNG、JPEG 或 WebP，且不超过 50 MB"
        ) from None
    return f"reference.{IMAGE_MIMES[mime]}", data, mime


def image_data_url(ref):
    image_file(ref)
    return ref.data_url
