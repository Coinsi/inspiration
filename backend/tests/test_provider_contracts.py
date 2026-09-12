"""Provider response tests use controlled HTTP responses, never paid API calls."""

import base64
import io
import json
import urllib.error

import pytest
from PIL import Image

from app.adapters.contracts import GenerationRequest
from app.adapters.generation.gpt_image import GptImageProvider
from app.core.errors import ProviderError
from app.modules.generation import media_engine


class Response(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


def stream(monkeypatch, events):
    body = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_, **__: Response(body))


def test_partial_image_is_not_completion(monkeypatch):
    encoded = base64.b64encode(media_engine.mock_image()).decode()
    stream(monkeypatch, [{"type": "image_generation.partial_image", "b64_json": encoded}])
    provider = GptImageProvider(token="test", config={"stream": True})
    with pytest.raises(ProviderError, match="未收到完成事件"):
        provider.submit(GenerationRequest(request_type="image", prompt="test"))


def test_stream_error_after_partial_is_failure(monkeypatch):
    stream(
        monkeypatch,
        [
            {"type": "image_generation.partial_image", "b64_json": "AAAA"},
            {"type": "error", "error": "interrupted"},
        ],
    )
    with pytest.raises(ProviderError, match="未保存预览图"):
        GptImageProvider(token="test").submit(
            GenerationRequest(request_type="image", prompt="test")
        )


def test_completed_image_and_usage_survive(monkeypatch):
    encoded = base64.b64encode(media_engine.mock_image()).decode()
    stream(
        monkeypatch,
        [
            {
                "type": "image_generation.completed",
                "b64_json": encoded,
                "usage": {"total_tokens": 10},
            }
        ],
    )
    provider = GptImageProvider(token="test", config={"stream": True})
    result = provider.poll(provider.submit(GenerationRequest(request_type="image", prompt="test")))
    assert result.status == "succeeded"
    assert result.outputs[0].data == media_engine.mock_image()
    assert result.cost_raw["usage"] == [{"total_tokens": 10}]


def test_fallback_only_for_rejected_stream_parameter(monkeypatch):
    provider = GptImageProvider(token="test", config={"stream": True})
    called = []
    monkeypatch.setattr(provider, "_generations", lambda *_: called.append(True) or {"data": []})

    def reject_stream(*_):
        raise ProviderError("unknown parameter", {"code": "unknown_parameter", "param": "stream"})

    monkeypatch.setattr(provider, "_generations_stream", reject_stream)
    provider._generate_one(GenerationRequest(request_type="image", prompt="test"))
    assert called == [True]

    def reject_quality(*_):
        raise ProviderError(
            "unknown parameter n in message", {"code": "unknown_parameter", "param": "quality"}
        )

    monkeypatch.setattr(provider, "_generations_stream", reject_quality)
    with pytest.raises(ProviderError):
        provider._generate_one(GenerationRequest(request_type="image", prompt="test"))
    assert called == [True]


def test_gpt_image_2_defaults_to_nonstreaming(monkeypatch):
    provider = GptImageProvider(token="test", config={"model": "gpt-image-2"})
    called = []
    monkeypatch.setattr(provider, "_generations", lambda *_: called.append(True) or {"data": []})
    provider._generate_one(GenerationRequest(request_type="image", prompt="test"))
    assert called == [True]


def test_missing_variants_and_corrupt_encoding_fail(monkeypatch):
    provider = GptImageProvider(token="test")
    monkeypatch.setattr(
        provider, "_call_with_n_fallback", lambda *_: {"data": [{"b64_json": "not-base64!"}]}
    )
    with pytest.raises(ProviderError, match="请求 2"):
        provider.submit(GenerationRequest(request_type="image", prompt="test", count=2))
    handle = provider.submit(GenerationRequest(request_type="image", prompt="test"))
    assert provider.poll(handle).status == "failed"


def test_http_error_retains_exact_parameter():
    error = urllib.error.HTTPError(
        "https://example.com",
        400,
        "Bad Request",
        {},
        io.BytesIO(
            json.dumps(
                {"error": {"code": "unknown_parameter", "param": "n", "message": "Unknown n"}}
            ).encode()
        ),
    )
    result = GptImageProvider._http_error(error)
    assert result.detail == {"code": "unknown_parameter", "param": "n"}


def test_validate_media_rejects_html_and_fake_video():
    with pytest.raises(ValueError, match="不能解码"):
        media_engine.validate_output(b"<html>gateway error</html>", "image")
    with pytest.raises(ValueError, match="有效 MP4"):
        media_engine.validate_output(media_engine.mock_image(), "video")
    buf = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(buf, "JPEG")
    png, dimensions = media_engine.validate_output(buf.getvalue(), "image")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and dimensions == {"width": 24, "height": 16}


def test_verified_local_copy_restores_legacy_blob(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from app.core.config import settings
    from app.storage import cas

    data = b"previously stored source"
    digest = hashlib.sha256(data).hexdigest()
    path = tmp_path / digest[:2] / digest
    path.parent.mkdir()
    path.write_bytes(data)
    monkeypatch.setattr(settings, "fs_storage_dir", str(tmp_path))

    def unavailable():
        raise RuntimeError("MinIO unavailable")

    monkeypatch.setattr(cas, "_minio", unavailable)
    blob = SimpleNamespace(hash=digest, storage_uri="legacy-bucket/key")
    assert cas.read_bytes(blob) == data
    path.write_bytes(b"wrong contents")
    with pytest.raises(RuntimeError, match="MinIO unavailable"):
        cas.read_bytes(blob)
