"""Pinned MediaCMS serializer shapes plus transport/privacy boundary regression checks."""

import hashlib
import importlib.util
import io
from pathlib import Path

import httpx
import pytest

from app.modules.library.providers.mediacms import (
    MediaCMSCancelled,
    MediaCMSClient,
    MediaCMSError,
    MediaCMSPrivacyError,
    token_for_upload,
)

BASE = "https://media.internal"


def detail(**overrides):
    # SingleMediaSerializer intentionally has no friendly_token.
    return {
        "state": "private",
        "encoding_status": "success",
        "media_type": "video",
        "duration": 3600,
        "thumbnail_url": "/media/original/thumbnails/thumb.jpg",
        "poster_url": "/media/original/thumbnails/poster.jpg",
        "preview_url": "/media/encoded/preview.mp4",
        "sprites_url": None,
        "original_media_url": "/media/original/source.mp4",
        "encodings_info": {
            "144": {},
            "360": {
                "h264": {
                    "url": "/media/encoded/360.mp4",
                    "status": "success",
                    "progress": 100,
                    "encoding_id": 42,
                    "size": "42 MB",
                    "title": "h264-360",
                }
            },
            "720": {
                "h264": {
                    "url": None,
                    "status": "running",
                    "progress": 12,
                    "encoding_id": 43,
                }
            },
        },
        "subtitles_info": [
            {
                "src": "/media/original/subtitles/captions.vtt",
                "srclang": "whisper",
                "label": "Transcription",
            }
        ],
        "hls_info": {
            "master_file": "/media/hls/index.m3u8",
            "360_playlist": "/media/hls/360/index.m3u8",
        },
        **overrides,
    }


def provider(handler, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return MediaCMSClient(BASE, "test-service-token", client=client, **kwargs)


def test_get_detail_retains_individual_encoding_status():
    def handle(request):
        assert request.url.path == "/api/v1/media/abc123"
        assert request.headers["Authorization"] == "Token test-service-token"
        assert request.extensions["timeout"] == {
            "connect": 10,
            "read": 60,
            "write": 120,
            "pool": 10,
        }
        return httpx.Response(200, json=detail())

    media = provider(handle).get_media("abc123")
    assert media.remote_id == "abc123"
    assert media.duration_seconds == 3600
    assert media.encodings[1].status == "running"
    assert len(media.ready_encodings) == 1
    assert media.ready_encodings[0].encoding_id == 42
    assert media.subtitles[0].url == BASE + "/media/original/subtitles/captions.vtt"
    assert media.hls["master_file"] == BASE + "/media/hls/index.m3u8"


def test_original_fallback_does_not_report_transcoding_as_complete():
    media = provider(
        lambda _: httpx.Response(
            200,
            json=detail(
                encoding_status="pending",
                encodings_info={
                    "360": {},
                    "0-original": {
                        "h264": {
                            "url": "/media/original/source.mp4",
                            "status": "success",
                            "progress": 100,
                        },
                    },
                },
            ),
        )
    ).get_media("abc123")
    assert media.encoding_status == "pending"
    assert not media.ready_encodings


def test_upload_is_streamed_and_uses_actual_multipart_fields(tmp_path, monkeypatch):
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"a" * (256 * 1024))
    reads = []
    opened = Path.open

    class Tracked(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= 64 * 1024
            return super().read(size)

    def tracked_open(value, *args, **kwargs):
        if value == path and args == ("rb",):
            return Tracked(b"a" * (256 * 1024))
        return opened(value, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)

    def handle(request):
        assert request.url.path == "/api/v1/media"
        body = request.content
        assert b'name="media_file"; filename="movie.mp4"' in body
        assert b'name="title"' in body
        assert b'name="state"' not in body  # read-only upstream field, never fake privacy
        assert b"Content-Type: video/mp4" in body
        return httpx.Response(
            201,
            json={
                "friendly_token": "abc123",
                "state": "private",
                "encoding_status": "pending",
                "media_type": "video",
            },
        )

    media = provider(handle, private_workflow_confirmed=True).upload(path, title="Test")
    assert media.remote_id == "abc123"
    assert len(reads) > 1


def test_upload_requires_private_deployment_and_rejects_non_private_result(tmp_path):
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"test")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(201, json={"friendly_token": "new123", "state": "public"})

    with pytest.raises(MediaCMSError, match="Confirm private"):
        provider(handle).upload(path, title="Test")
    assert not calls
    with pytest.raises(MediaCMSPrivacyError) as raised:
        provider(handle, private_workflow_confirmed=True).upload(path, title="Test")
    assert raised.value.remote_token == "new123"  # persist for targeted cleanup


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/steal",
        "//evil.example/steal",
        "file:///etc/passwd",
        "https://name:secret@media.internal/media/a",
        "https://media.internal:444/media/a",
        "https://media.internal\\@evil.example/a",
        "\nhttps://media.internal/a",
    ],
)
def test_untrusted_output_urls_rejected_before_network(url, tmp_path):
    calls = []
    client = provider(lambda req: calls.append(req))
    with pytest.raises(MediaCMSError):
        client.download(url, tmp_path / "output", max_bytes=10)
    assert not calls


def test_allowed_cdn_never_receives_any_client_credentials(tmp_path):
    def handle(request):
        assert request.url.host == "cdn.internal"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "proxy-authorization" not in request.headers
        assert "secret" not in str(request.url)
        return httpx.Response(200, content=b"video")

    http = httpx.Client(
        transport=httpx.MockTransport(handle),
        auth=("basic-user", "secret"),
        cookies={"sessionid": "secret"},
        headers={"Authorization": "secret", "Proxy-Authorization": "secret"},
        params={"secret": "value"},
        follow_redirects=True,
    )
    client = MediaCMSClient(
        BASE, "token", client=http, allowed_media_origins=["https://cdn.internal"]
    )
    output = tmp_path / "video"
    assert client.download("https://cdn.internal/output", output, max_bytes=10) == 5
    assert output.read_bytes() == b"video"


def test_redirects_are_never_followed_even_on_injected_client(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/"})

    http = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)
    client = MediaCMSClient(BASE, "token", client=http)
    with pytest.raises(MediaCMSError, match="HTTP 302"):
        client.download("/media/source", tmp_path / "output", max_bytes=10)
    assert len(calls) == 1


def test_download_size_limits_cancel_and_preserve_previous_output(tmp_path):
    output = tmp_path / "existing"
    output.write_bytes(b"preserve")
    client = provider(lambda _: httpx.Response(200, content=b"oversize"))
    with pytest.raises(MediaCMSError, match="size limit"):
        client.download("/media/source", output, max_bytes=3)
    with pytest.raises(MediaCMSCancelled):
        client.download("/media/source", output, max_bytes=30, canceled=lambda: True)
    assert output.read_bytes() == b"preserve"
    assert list(tmp_path.iterdir()) == [output]


def test_stream_limit_without_content_length_and_interrupt(tmp_path):
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a" * 65536
            yield b"b" * 65536

    client = provider(lambda _: httpx.Response(200, stream=Stream()))
    with pytest.raises(MediaCMSError, match="size limit"):
        client.download("/media/source", tmp_path / "output", max_bytes=70000)
    counter = 0

    def cancel():
        nonlocal counter
        counter += 1
        return counter > 2

    with pytest.raises(MediaCMSCancelled):
        client.download("/media/source", tmp_path / "output", max_bytes=200000, canceled=cancel)
    assert not list(tmp_path.iterdir())


def test_missing_media_is_distinguished_from_permission_errors():
    client = provider(lambda _: httpx.Response(400, json={"detail": "media file does not exist"}))
    assert client.get_media("abc123", missing=True) is None
    assert client.delete_media("abc123") is False
    with pytest.raises(MediaCMSError):
        client.get_media("abc123")
    client = provider(lambda _: httpx.Response(401, json={"detail": "bad permissions"}))
    with pytest.raises(MediaCMSError) as raised:
        client.get_media("abc123", missing=True)
    assert raised.value.status_code == 401


def test_encode_and_delete_use_documented_methods():
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            assert request.content == b"type=encode&encoding_profiles=7"
            return httpx.Response(201, json={"detail": "media will be encoded"})
        assert request.method == "DELETE"
        return httpx.Response(204)

    client = provider(handle)
    client.request_encode("abc123", profile_id=7)
    assert client.delete_media("abc123") is True
    assert len(calls) == 2


def test_transport_errors_do_not_leak_secrets_or_retry_writes(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"test")
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret upstream body", request=request)

    with pytest.raises(MediaCMSError) as raised:
        provider(handle, private_workflow_confirmed=True).upload(path, title="Test")
    assert "secret" not in str(raised.value)
    assert raised.value.retryable
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,retryable",
    [(401, False), (403, False), (422, False), (429, True), (500, True), (503, True)],
)
def test_http_error_retryability_retains_slot_only_for_temporary_failures(status, retryable):
    client = provider(lambda _: httpx.Response(status, json={"detail": "private upstream detail"}))
    with pytest.raises(MediaCMSError) as raised:
        client.get_media("abc123")
    assert raised.value.retryable is retryable
    assert "private upstream detail" not in str(raised.value)


def test_validation_errors_are_not_retryable():
    client = provider(lambda _: httpx.Response(200, json=detail(encoding_status="invalid")))
    with pytest.raises(MediaCMSError) as raised:
        client.get_media("abc123")
    assert not raised.value.retryable


def test_download_timeout_is_retryable_and_cleans_partial_file(tmp_path):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a" * 65536
            raise httpx.ReadTimeout("private transport detail")

    client = provider(lambda _: httpx.Response(200, stream=BrokenStream()))
    with pytest.raises(MediaCMSError) as raised:
        client.download("/media/source", tmp_path / "output", max_bytes=131072)
    assert raised.value.retryable
    assert "private transport detail" not in str(raised.value)
    assert not list(tmp_path.iterdir())


def test_idempotent_bridge_upload_and_manifest(tmp_path):
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"test")
    remote = token_for_upload("run_123")
    assert remote == MediaCMSClient.upload_remote_id("run_123")
    assert remote != token_for_upload("run_124")

    def handle(request):
        if request.method == "POST":
            assert request.url.path == "/api/inspiration/v1/media/"
            assert b'name="source_sha256"' in request.content
            assert b'name="upload_key"' in request.content
            return httpx.Response(
                200,
                json={"friendly_token": remote, "state": "private", "encoding_status": "pending"},
            )
        assert request.url.path.endswith("/manifest")
        return httpx.Response(
            200,
            json={
                "remote_id": remote,
                "encoding_status": "success",
                "processing": {
                    "dispatch": "submitted",
                    "complete": True,
                    "status": "success",
                    "expected": 1,
                    "terminal": 1,
                    "pending": 0,
                    "running": 0,
                    "failed": 0,
                },
                "preview": None,
                "thumbnail": None,
                "poster": None,
                "subtitles": [],
                "encodings": [
                    {
                        "key": "encoding-7",
                        "url": f"/api/inspiration/v1/media/{remote}/files/encoding-7",
                        "mime": "video/mp4",
                        "resolution": 360,
                        "id": 7,
                    }
                ],
            },
        )

    client = provider(handle, private_workflow_confirmed=True)
    result = client.upload(
        path, title="Test", upload_key="run_123", source_sha256=hashlib.sha256(b"test").hexdigest()
    )
    assert result.remote_id == remote
    assert client.manifest(remote)["encodings"][0]["url"].startswith(BASE)


def test_bridge_transcription_does_not_claim_success_from_empty_subtitles():
    def handle(request):
        assert request.url.path == "/api/inspiration/v1/media/abc123/transcription"
        if request.method == "POST":
            assert request.content == b'{"retry":true}'
            return httpx.Response(202, json={"request_id": 7, "status": "pending"})
        return httpx.Response(200, json={"request_id": None, "status": "not_requested"})

    client = provider(handle)
    assert client.get_transcription("abc123")["status"] == "not_requested"
    assert client.request_transcription("abc123", retry=True)["status"] == "pending"


def test_bridge_file_resolver_refuses_paths_outside_media_root(tmp_path):
    module_path = Path(__file__).resolve().parents[2] / "services/mediacms-bridge/outputs.py"
    spec = importlib.util.spec_from_file_location("mediacms_bridge_outputs", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "media"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    valid = root / "preview.mp4"
    valid.write_bytes(b"video")
    assert module.safe_media_path(secret, root) is None
    assert module.safe_media_path(root / "../secret.txt", root) is None
    assert module.safe_media_path(root, root) is None
    item, actual = module.output_item("abc123", "preview", valid, root)
    assert actual == valid
    assert item["size_bytes"] == 5
    assert str(tmp_path) not in str(item)


def test_malformed_status_and_poisoned_manifest_fail_closed():
    client = provider(lambda _: httpx.Response(200, json=detail(encoding_status="done")))
    with pytest.raises(MediaCMSError, match="unknown encoding status"):
        client.get_media("abc123")
    client = provider(
        lambda _: httpx.Response(
            200,
            json={
                "remote_id": "abc123",
                "encoding_status": "success",
                "encodings": [],
                "subtitles": [{"key": "subtitle-1", "url": "https://evil.example/"}],
            },
        )
    )
    with pytest.raises(MediaCMSError, match="outside allowed"):
        client.manifest("abc123")


@pytest.mark.parametrize(
    "overrides",
    [
        {"complete": True, "status": "running", "terminal": 0},
        {"complete": True, "pending": 1},
        {"complete": True, "terminal": 0},
        {"complete": True, "status": "fail", "failed": 0},
        {"complete": True, "dispatch": "submitting"},
    ],
)
def test_contradictory_completion_checkpoint_is_rejected(overrides):
    progress = {
        "dispatch": "submitted",
        "complete": True,
        "status": "success",
        "expected": 1,
        "terminal": 1,
        "pending": 0,
        "running": 0,
        "failed": 0,
        **overrides,
    }
    client = provider(
        lambda _: httpx.Response(
            200,
            json={
                "remote_id": "abc123",
                "encoding_status": "success",
                "encodings": [],
                "subtitles": [],
                "processing": progress,
            },
        )
    )
    with pytest.raises(MediaCMSError, match="completion checkpoint"):
        client.manifest("abc123")
