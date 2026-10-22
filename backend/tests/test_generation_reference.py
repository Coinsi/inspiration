import asyncio
import io
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AppError, app_error_handler
from app.modules.media import generation_reference as media


@pytest.fixture
def grant_client(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-signing-key-" * 4)
    app = FastAPI()
    app.include_router(media.router, prefix="/api/v1")
    app.add_exception_handler(AppError, app_error_handler)
    blob = SimpleNamespace(mime="image/png")
    db = SimpleNamespace(get=lambda cls, key: blob if key == "a" * 64 else None)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(media.cas, "read_bytes", lambda b: b"png-bytes")
    with media.reference_origin("https://studio.example.com"), TestClient(app) as client:
        yield client, blob


def test_grant_is_single_blob_expiring_and_not_a_login_token(grant_client, monkeypatch):
    client, blob = grant_client
    monkeypatch.setattr(media.time, "time", lambda: 10000)
    url = media.reference_url("a" * 64)
    response = client.get(url)
    assert response.status_code == 200 and response.content == b"png-bytes"
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get(url.replace("a" * 64, "b" * 64)).status_code == 403
    assert client.get(url.replace("expires=13600", "expires=13601")).status_code == 403
    assert client.get(urlsplit(url).path).status_code == 422
    blob.mime = "text/html"
    assert client.get(url).status_code == 404
    blob.mime = "image/png"
    monkeypatch.setattr(media.time, "time", lambda: 13600)
    assert client.get(url).status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://[::1]",
        "https://user:pass@example.com",
        "https://example.com?token=x",
    ],
)
def test_invalid_public_origin_cannot_publish_grants(monkeypatch, origin):
    monkeypatch.setattr(settings, "secret_key", "test-signing-key-" * 4)
    with media.reference_origin(origin):
        assert not media.public_base()
        with pytest.raises(ValueError):
            media.reference_url("a" * 64)


def test_default_secret_cannot_publish_grants(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "dev-secret-change-me")
    with media.reference_origin("https://studio.example.com"):
        assert not media.public_base()


def test_video_grant_streams_ranges_and_head_without_reading_entire_blob(grant_client, monkeypatch):
    client, blob = grant_client
    blob.mime = "video/mp4"
    blob.size_bytes = 10
    reads = []

    @contextmanager
    def open_range(item, start, length):
        reads.append((start, length))
        yield io.BytesIO(b"0123456789"[start : start + length])

    monkeypatch.setattr(media.cas, "open_range", open_range)
    monkeypatch.setattr(media.cas, "read_bytes", lambda _: pytest.fail("Video must be streamed"))
    url = media.reference_url("a" * 64)
    response = client.get(url, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert client.get(url, headers={"Range": "bytes=-3"}).content == b"789"
    assert client.get(url, headers={"Range": "bytes=10-"}).status_code == 416
    assert client.get(url, headers={"Range": "bytes=0-1,3-4"}).status_code == 416
    assert client.get(url).content == b"0123456789"
    before = len(reads)
    response = client.head(url)
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-length"] == "10"
    assert len(reads) == before
    assert client.get(url.replace("a" * 64, "b" * 64)).status_code == 403


def test_request_origins_are_dynamic_isolated_and_ignore_forwarded_headers(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-signing-key-" * 4)
    app = FastAPI()
    app.add_middleware(media.ReferenceOriginMiddleware)

    @app.get("/origin")
    async def origin():
        before = media.public_base()
        await asyncio.sleep(0)
        return {"before": before, "after": media.public_base()}

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            results = await asyncio.gather(
                *[
                    client.get(
                        f"https://{host}/origin",
                        headers={
                            "X-Forwarded-Host": "attacker.example.com",
                            "X-Forwarded-Proto": "http",
                        },
                    )
                    for host in ["old.example.com", "new.example.com"]
                ]
            )
            assert [r.json() for r in results] == [
                {"before": f"https://{host}", "after": f"https://{host}"}
                for host in ["old.example.com", "new.example.com"]
            ]
            r = await client.get(
                "http://localhost/origin",
                headers={
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "public.example.com",
                },
            )
            assert r.json() == {"before": "", "after": ""}

    asyncio.run(check())
    assert media.public_base() == ""
