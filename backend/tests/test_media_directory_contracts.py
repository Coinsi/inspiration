"""Offline vendor contracts for the media choices in Settings."""

from types import SimpleNamespace

import httpx
import pytest

from app.adapters.contracts import GenerationRequest, JobHandle, ReferenceImage, generation_registry
from app.adapters.generation import provider_media as adapters
from app.core.errors import ProviderError
from app.modules.generation.channels import discover_connection
from app.modules.generation.model_routing import resumable_profile
from app.modules.generation.provider_templates import BUILTINS, TemplateIn

URL = "https://media.example/test"
REF = ReferenceImage(blob_hash="a" * 64, data_url="data:image/png;base64,YQ==", role="reference")


def test_screenshot_suppliers_have_callable_routes():
    image = {
        "openai",
        "google",
        "minimax",
        "wavespeed",
        "openrouter",
        "byteplus",
        "xai",
        "ark",
        "kling",
        "zhipu",
        "bailian-media",
    }
    video = {
        "google",
        "minimax",
        "openrouter",
        "byteplus",
        "xai",
        "ark",
        "kling",
        "zhipu",
        "bailian-media",
        "heygen",
        "bfl",
        "vidu",
        "openai-compatible-video",
    }
    for modality, required in (("image", image), ("video", video)):
        for key in required:
            template = TemplateIn.model_validate(BUILTINS[key])
            route = next(r for r in template.routes if r.modality == modality)
            assert route.protocol in generation_registry.names()


@pytest.mark.parametrize(
    ("adapter", "create_path", "receipt", "query_path", "finished"),
    [
        (
            adapters.MiniMaxImage,
            "/image_generation",
            {"base_resp": {"status_code": 0}, "data": {"image_urls": [URL]}},
            None,
            None,
        ),
        (adapters.XaiImage, "/images/edits", {"data": [{"url": URL}]}, None, None),
        (adapters.ZhipuImage, "/images/generations", {"data": [{"url": URL}]}, None, None),
        (
            adapters.ZhipuVideo,
            "/videos/generations",
            {"id": "abc"},
            "/async-result/abc",
            {"task_status": "SUCCESS", "video_result": [{"url": URL}]},
        ),
        (
            adapters.XaiVideo,
            "/videos/generations",
            {"request_id": "abc"},
            "/videos/abc",
            {"status": "done", "video": {"url": URL}},
        ),
        (
            adapters.WaveSpeedImage,
            "/team/model",
            {"data": {"id": "abc"}},
            "/predictions/abc/result",
            {"data": {"status": "completed", "outputs": [URL]}},
        ),
        (
            adapters.ViduVideo,
            "/img2video",
            {"task_id": "abc"},
            "/tasks/abc/creations",
            {"state": "success", "creations": [{"url": URL}]},
        ),
        (
            adapters.HeyGenVideo,
            "/models/videos",
            {"data": {"video_id": "abc"}},
            "/models/videos/abc",
            {"data": {"status": "completed", "video_url": URL}},
        ),
        (
            adapters.KlingVideo,
            "/videos/image2video",
            {"code": 0, "data": {"task_id": "abc"}},
            "/videos/image2video/abc",
            {
                "code": 0,
                "data": {"task_status": "succeed", "task_result": {"videos": [{"url": URL}]}},
            },
        ),
        (
            adapters.KlingImage,
            "/images/generations",
            {"code": 0, "data": {"task_id": "abc"}},
            "/images/generations/abc",
            {
                "code": 0,
                "data": {"task_status": "succeed", "task_result": {"images": [{"url": URL}]}},
            },
        ),
        (
            adapters.BailianVideo,
            "/services/aigc/video-generation/video-synthesis",
            {"output": {"task_id": "abc"}},
            "/tasks/abc",
            {"output": {"task_status": "SUCCEEDED", "video_url": URL}},
        ),
        (
            adapters.BflVideo,
            "/v1/model",
            {"id": "abc", "polling_url": "https://api.eu1.bfl.ai/v1/get_result?id=abc"},
            "/v1/get_result",
            {"status": "Ready", "result": {"sample": URL}},
        ),
        (
            adapters.OpenRouterVideo,
            "/videos",
            {"id": "abc"},
            "/videos/abc",
            {"status": "completed"},
        ),
    ],
)
def test_create_reference_and_resume(
    monkeypatch, adapter, create_path, receipt, query_path, finished
):
    import json

    body = None
    seen = []
    base = "https://api.bfl.ai" if adapter is adapters.BflVideo else "https://example.test"

    def respond(req):
        nonlocal body
        seen.append((req.method, req.url.path))
        if req.method == "POST":
            assert req.url.path == create_path
            body = json.loads(req.content)
            return httpx.Response(200, json=receipt)
        if req.url.path.endswith("/content"):
            assert req.url.params["index"] == "0"
            return httpx.Response(200, content=b"mock video")
        assert req.url.path == query_path
        return httpx.Response(200, json=finished)

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **k: client(transport=httpx.MockTransport(respond), **k)
    )
    cfg = {
        "model": "team/model" if adapter is adapters.WaveSpeedImage else "model",
        "max_reference_images": adapter.max_refs,
    }
    token = "access:" + "s" * 32 if issubclass(adapter, adapters.Kling) else "test"
    provider = adapter(endpoint=base, token=token, config=cfg)
    request = GenerationRequest(
        request_type=provider.modality,
        prompt="morning light",
        references=[REF] if provider.max_refs else [],
    )
    handle = provider.submit(request)
    inputs = body.get("input", body)
    assert inputs["prompt"] == request.prompt
    if query_path:
        assert resumable_profile({"protocol": adapter.name})
        handle = JobHandle(external_job_id=handle.external_job_id)
    result = provider.poll(handle)
    assert result.status == "succeeded"
    assert len(result.outputs) == 1 and (
        result.outputs[0].url == URL or result.outputs[0].data == b"mock video"
    )
    assert len([r for r in seen if r[0] == "POST"]) == 1


def test_bfl_polling_scope_and_role_validation():
    provider = adapters.BflVideo(endpoint="https://api.bfl.ai", token="test")
    with pytest.raises(ProviderError):
        provider.polling_origin("https://unrelated.test/v1/get_result?id=t")


def test_discovery_returns_names_and_requests_only_current_output(monkeypatch):
    from app.core.crypto import encrypt

    def upstream(request):
        assert request.url.params.get("output_modalities") == "video"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "vendor/video",
                        "name": "Video",
                        "architecture": {"output_modalities": ["video"]},
                    },
                    {"id": "manual-id"},
                ]
            },
        )

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **k: client(transport=httpx.MockTransport(upstream), **k)
    )
    ch = SimpleNamespace(endpoint="https://example.test", credentials_encrypted=encrypt("fixture"))
    result = discover_connection(ch, "openrouter", purpose="video")
    assert result["models"] == ["manual-id", "vendor/video"]
    assert result["details"] == [
        {"id": "manual-id"},
        {"id": "vendor/video", "name": "Video", "outputs": ["video"]},
    ]
