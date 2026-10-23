"""Short-lived, single-file image/video grants for providers; never login tokens."""

import hashlib
import hmac
import ipaddress
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.requests import Request

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import Forbidden, NotFound
from app.models.storage import Blob
from app.storage import cas

router = APIRouter(tags=["media"])
TTL = 3600
_origin: ContextVar[str] = ContextVar("generation_reference_origin", default="")


@contextmanager
def reference_origin(value):
    """Request-local origin, also restored from the immutable job snapshot in workers."""
    token = _origin.set(value or "")
    try:
        yield
    finally:
        _origin.reset(token)


class ReferenceOriginMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Proxy middleware has already established scheme. Do not interpret arbitrary
        # X-Forwarded-Host/Proto headers here. The ingress must validate Host.
        with reference_origin(str(Request(scope).base_url)):
            await self.app(scope, receive, send)


def public_base():
    value = (settings.generation_reference_public_base_url or _origin.get()).strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or host == "localhost"
            or host.endswith((".localhost", ".local"))
        ):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if "." not in host:
                return ""
        # Never publish grants signed by the development default.
        if settings.secret_key == "dev-secret-change-me" or len(settings.secret_key) < 32:
            return ""
        return value
    except ValueError:
        return ""


def _signature(blob_hash, expires):
    message = f"generation-reference:v1:{blob_hash}:{expires}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def reference_url(blob_hash):
    base = public_base()
    if not base or not re.fullmatch(r"[a-f0-9]{64}", blob_hash):
        raise ValueError("请通过公网 HTTPS 站点提交，并使用正式 SECRET_KEY")
    expires = int(time.time()) + TTL
    query = urlencode({"expires": expires, "signature": _signature(blob_hash, expires)})
    return f"{base}/api/v1/generation-references/{blob_hash}?{query}"


@router.api_route("/generation-references/{blob_hash}", methods=["GET", "HEAD"])
def get_reference(
    blob_hash: str,
    request: Request,
    db: Annotated[Session, Depends(get_db, scope="function")],
    expires: int = Query(...),
    signature: str = Query(..., max_length=64),
    range: str | None = Header(None),
):
    now = int(time.time())
    if (
        settings.secret_key == "dev-secret-change-me"
        or len(settings.secret_key) < 32
        or not re.fullmatch(r"[a-f0-9]{64}", blob_hash)
        or not now < expires <= now + TTL
        or not hmac.compare_digest(signature, _signature(blob_hash, expires))
    ):
        raise Forbidden("参考素材链接无效或已过期")
    blob = db.get(Blob, blob_hash)
    if blob is None or blob.mime not in {
        "image/png",
        "image/jpeg",
        "image/webp",
        "video/mp4",
        "video/webm",
        "video/quicktime",
    }:
        raise NotFound("参考素材不存在或格式不支持")
    if blob.mime.startswith("video/"):
        return stream_reference(blob, range, request.method == "HEAD")
    return Response(
        cas.read_bytes(blob),
        media_type=blob.mime,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


def stream_reference(blob, range_header, head=False):
    """Bounded reads, including byte ranges required by upstream video readers."""
    size = blob.size_bytes
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    start, end = 0, size - 1
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if not match or not any(match.groups()):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        left, right = match.groups()
        start = int(left) if left else max(0, size - int(right))
        end = min(int(right), size - 1) if left and right else size - 1
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)
    if head:
        return Response(
            status_code=206 if range_header else 200, headers=headers, media_type=blob.mime
        )
    manager = cas.open_range(blob, start, length)
    try:
        stream = manager.__enter__()
    except Exception as exc:
        raise NotFound("参考视频不可读取") from exc
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            manager.__exit__(None, None, None)

    def chunks():
        remaining = length
        try:
            while remaining:
                data = stream.read(min(1024 * 1024, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            close()

    return StreamingResponse(
        chunks(),
        status_code=206 if range_header else 200,
        media_type=blob.mime,
        headers=headers,
        background=BackgroundTask(close),
    )
