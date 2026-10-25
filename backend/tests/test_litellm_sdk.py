"""Real pinned SDK against a local HTTP server; no external requests or paid generation."""

import base64
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.adapters.assist.streaming import progress
from app.adapters.contracts import GenerationRequest, JobHandle, ReferenceImage
from app.adapters.generation.litellm_media import LiteLLMMediaProvider
from app.adapters.model_sdk import chat
from app.core.errors import CapabilityUnsupported, ProviderError

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRZkAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def local_connections(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@contextmanager
def upstream(reply):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.command, self.path, dict(self.headers), body))
            status, content_type, result = reply(self.command, self.path, body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        do_POST = handle_request
        do_GET = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def js(value):
    return 200, "application/json", json.dumps(value).encode()


def test_real_sdk_stream_usage_and_per_request_credentials():
    def reply(*_):
        events = [
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test",
                "choices": [
                    {"index": 0, "delta": {"content": '{"reply":"ok"}'}, "finish_reason": None}
                ],
            },
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        ]
        body = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
        return 200, "text/event-stream", body + b"data: [DONE]\n\n"

    updates = []
    with upstream(reply) as (endpoint, calls), progress(updates.append):
        for token in ("test-key-a", "test-key-b"):
            content, usage = chat("system", "hi", endpoint, token, "test", max_output_tokens=40)
            assert content == '{"reply":"ok"}'
            assert usage["total_tokens"] == 7
        assert [c[2]["Authorization"] for c in calls] == ["Bearer test-key-a", "Bearer test-key-b"]
        assert all(c[1] == "/v1/chat/completions" for c in calls)
        assert json.loads(calls[0][3])["stream"] is True
    assert updates


@pytest.mark.parametrize("status", [401, 504])
def test_sdk_does_not_replay_failed_submit(status):
    def reply(*_):
        return status, "application/json", b'{"error":{"message":"secret upstream details"}}'

    with upstream(reply) as (endpoint, calls):
        with pytest.raises(ProviderError) as exc:
            chat("system", "hi", endpoint, "test-key", "test")
        assert "secret" not in str(exc.value)
        assert len(calls) == 1


def test_real_sdk_image_generation_and_edit_preserve_reference_and_mask():
    def reply(*_):
        return js({"created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    ref = ReferenceImage(
        blob_hash="a" * 64, data_url="data:image/png;base64," + base64.b64encode(PNG).decode()
    )
    with upstream(reply) as (endpoint, calls):
        provider = LiteLLMMediaProvider(
            endpoint,
            "test-key",
            {
                "model": "gpt-image-1",
                "modality": "image",
                "max_reference_images": 2,
                "mask_editing": True,
            },
        )
        for references, mask in (([], None), ([ref], ref)):
            handle = provider.submit(
                GenerationRequest(
                    request_type="image", prompt="draw", references=references, mask=mask
                )
            )
            assert provider.poll(handle).outputs[0].data == PNG
        assert calls[0][1] == "/v1/images/generations"
        assert calls[1][1] == "/v1/images/edits"
        assert PNG in calls[1][3]
        assert b'name="mask"' in calls[1][3]


def test_real_sdk_video_resume_without_resubmission():
    def reply(method, path, body):
        if path.endswith("/content"):
            return 200, "video/mp4", b"video-result"
        return js(
            {
                "id": "upstream-job",
                "object": "video",
                "created_at": 1,
                "model": "sora-2",
                "status": "queued" if method == "POST" else "completed",
                "size": "1280x720",
                "seconds": "4",
            }
        )

    ref = ReferenceImage(
        blob_hash="a" * 64, data_url="data:image/png;base64," + base64.b64encode(PNG).decode()
    )
    config = {"model": "sora-2", "modality": "video", "max_reference_images": 1}
    with upstream(reply) as (endpoint, calls):
        provider = LiteLLMMediaProvider(endpoint, "test-key", config)
        handle = provider.submit(
            GenerationRequest(
                request_type="video", prompt="move", references=[ref], params={"duration": 4}
            )
        )
        recovered = LiteLLMMediaProvider(endpoint, "test-key", config)
        result = recovered.poll(JobHandle(external_job_id=handle.external_job_id))
        assert result.outputs[0].data == b"video-result"
        assert [c[0] for c in calls] == ["POST", "GET", "GET"]
        assert calls[1][1] == "/v1/videos/upstream-job"
        assert b'name="input_reference"' in calls[0][3]
        assert PNG in calls[0][3]


def test_video_reference_is_rejected_not_silently_dropped():
    provider = LiteLLMMediaProvider(config={"model": "sora-2", "modality": "video"})
    with pytest.raises(CapabilityUnsupported):
        provider.submit(
            GenerationRequest(
                request_type="video",
                prompt="edit",
                references=[
                    ReferenceImage(blob_hash="a" * 64, kind="video"),
                ],
            )
        )


@pytest.mark.parametrize("reason", ["length", "content_filter", None])
def test_real_sdk_incomplete_stream_is_never_an_action(reason):
    def reply(*_):
        event = {
            "id": "c",
            "created": 1,
            "model": "test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": '{"partial":'}, "finish_reason": reason}],
        }
        return 200, "text/event-stream", b"data: " + json.dumps(event).encode() + b"\n\n"

    with upstream(reply) as (endpoint, _), pytest.raises(ProviderError):
        chat("system", "hi", endpoint, "test", "test")


def test_gemini_video_maps_reference_bytes_and_duration():
    def reply(*_):
        return js({"name": "models/veo-3.0-generate-preview/operations/test-operation"})

    ref = ReferenceImage(
        blob_hash="a" * 64, data_url="data:image/png;base64," + base64.b64encode(PNG).decode()
    )
    with upstream(reply) as (endpoint, calls):
        provider = LiteLLMMediaProvider(
            endpoint.removesuffix("/v1"),
            "test-key",
            {
                "model": "veo-3.0-generate-preview",
                "modality": "video",
                "sdk_provider": "gemini",
                "max_reference_images": 1,
            },
        )
        handle = provider.submit(
            GenerationRequest(
                request_type="video",
                prompt="move",
                references=[ref],
                params={"duration": 8, "size": "1280x720"},
            )
        )
        assert handle.external_job_id
        body = json.loads(calls[0][3])
        assert base64.b64decode(body["instances"][0]["image"]["bytesBase64Encoded"]) == PNG
        assert body["parameters"]["durationSeconds"] == 8


def test_resumability_and_channel_validation():
    from pydantic import ValidationError

    from app.modules.generation.channels import ModelIn
    from app.modules.generation.model_routing import resumable_profile

    assert resumable_profile({"protocol": "litellm", "modality": "video"})
    assert not resumable_profile({"protocol": "litellm", "modality": "image"})
    model = ModelIn(
        name="Veo",
        model="veo-3",
        protocol="litellm",
        modality="video",
        sdk_provider="gemini",
        max_reference_images=1,
        parameters={"duration": [8]},
    )
    assert model.sdk_provider == "gemini"
    with pytest.raises(ValidationError):
        ModelIn(**{**model.model_dump(), "max_reference_videos": 1})


def test_gemini_text_uses_native_stream_and_keeps_usage():
    def reply(*_):
        value = {
            "candidates": [
                {
                    "index": 0,
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": '{"reply":"ok"}'},
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 3,
                "candidatesTokenCount": 4,
                "totalTokenCount": 7,
            },
        }
        return 200, "text/event-stream", b"data: " + json.dumps(value).encode() + b"\n\n"

    with upstream(reply) as (endpoint, calls):
        content, usage = chat(
            "system",
            "hi",
            endpoint.removesuffix("/v1"),
            "test-key",
            "gemini-2.5-flash",
            provider="gemini",
        )
        assert content == '{"reply":"ok"}'
        assert usage["total_tokens"] == 7
        assert ":streamGenerateContent" in calls[0][1]


@pytest.mark.parametrize("modality,model", [("image", "gpt-image-1"), ("video", "sora-2")])
def test_media_504_is_not_automatically_retried(modality, model):
    def reply(*_):
        return 504, "application/json", b'{"error":{"message":"timeout"}}'

    with upstream(reply) as (endpoint, calls):
        provider = LiteLLMMediaProvider(
            endpoint, "test-key", {"model": model, "modality": modality}
        )
        with pytest.raises(ProviderError, match="504"):
            provider.submit(GenerationRequest(request_type=modality, prompt="test"))
        assert len(calls) == 1


def test_sdk_estimated_usage_is_not_reported_as_confirmed_usage():
    def reply(*_):
        event = {
            "id": "c",
            "created": 1,
            "model": "test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "{}"}, "finish_reason": "stop"}],
        }
        return 200, "text/event-stream", b"data: " + json.dumps(event).encode() + b"\n\n"

    with upstream(reply) as (endpoint, _):
        content, usage = chat("system", "hi", endpoint, "test-key", "test")
        assert content == "{}" and usage is None


def test_anthropic_native_stream_is_normalized():
    def reply(*_):
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-20250514",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 3, "output_tokens": 0},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "{}"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 4},
            },
            {"type": "message_stop"},
        ]
        body = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events)
        return 200, "text/event-stream", body.encode()

    with upstream(reply) as (endpoint, calls):
        content, usage = chat(
            "system",
            "hi",
            endpoint.removesuffix("/v1"),
            "test-key",
            "claude-sonnet-4-20250514",
            provider="anthropic",
        )
        assert content == "{}" and usage["total_tokens"] == 7
        assert json.loads(calls[0][3])["model"] == "claude-sonnet-4-20250514"
