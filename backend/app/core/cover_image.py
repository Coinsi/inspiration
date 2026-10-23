"""Portable, bounded cover images shared by prompts and skills."""
from typing import Annotated
from urllib.parse import urlsplit
from pydantic import AfterValidator, Field


def validate_cover(value: str) -> str:
    if not value:
        return value
    if value.startswith("data:image/jpeg;base64,"):
        import base64
        try:
            raw = base64.b64decode(value.split(",", 1)[1], validate=True)
            if raw.startswith(b"\xff\xd8\xff"):
                return value
        except ValueError:
            pass
        raise ValueError("无效的封面图片")
    url = urlsplit(value)
    if len(value) <= 2048 and url.scheme in ("https", "http") and url.hostname and not url.username and not url.password:
        return value
    raise ValueError("请使用 HTTP(S) 图片地址或上传图片")


CoverImage = Annotated[str, Field(max_length=700000), AfterValidator(validate_cover)]
