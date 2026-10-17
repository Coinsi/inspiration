"""Contract regression for JSON video.v1; these tests never call a paid service."""

import json

import httpx
import pytest

from app.adapters.contracts import GenerationRequest, JobHandle, ReferenceImage
from app.adapters.generation.openai_video import OpenAIVideoProvider
from app.adapters.generation.video_v1 import VideoV1Provider
from app.core.errors import CapabilityUnsupported, ProviderError
from app.modules.generation.channels import ModelIn


def provider():
    return VideoV1Provider(
        endpoint="https://video.example/v1",
        token="secret-for-test",
        config={
            "model": "S-2.5-满血",
            "param_schema": {
                "duration": {"enum": [4, 5, 30]},
                "resolution": {"enum": ["720p"]},
                "aspect_ratio": {"enum": ["16:9", "9:16"]},
            },
        },
    )


def transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(**kw, transport=httpx.MockTransport(handler))
    )


def test_json_wire_and_poll_download_do_not_resubmit_or_leak_auth(monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.method == "POST":
            assert request.headers["content-type"] == "application/json"
            assert json.loads(request.content) == {
                "version": "video.v1",
                "model": "S-2.5-满血",
                "operation": "text_to_video",
                "prompt": "a cat",
                "duration_seconds": 4,
                "resolution": "720p",
                "aspect_ratio": "16:9",
            }
            return httpx.Response(200, json={"id": "task-1"})
        if request.url.host == "cdn.example":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"video bytes validated later by media engine")
        if request.url.path.endswith("/content"):
            assert request.url.params["download"] == "1"
            return httpx.Response(302, headers={"Location": "https://cdn.example/result.mp4"})
        return httpx.Response(200, json={"status": "completed"})

    transport(monkeypatch, handler)
    p = provider()
    h = p.submit(
        GenerationRequest(
            request_type="video",
            prompt="a cat",
            provider_params={
                "duration": 4,
                "resolution": "720p",
                "aspect_ratio": "16:9",
            },
        )
    )
    assert p.poll(h).status == "succeeded"
    assert sum(method == "POST" for method, _ in calls) == 1


@pytest.mark.parametrize("values", [{}, {"duration": 3}, {"duration": 4, "size": "1280x720"}])
def test_invalid_or_implicit_duration_rejected_before_network(values):
    with pytest.raises(CapabilityUnsupported):
        provider().submit(
            GenerationRequest(request_type="video", prompt="cat", provider_params=values)
        )


def test_reference_not_falsely_advertised_or_submitted():
    p = provider()
    assert not p.capabilities().max_reference_images
    with pytest.raises(CapabilityUnsupported, match="公网地址"):
        p.submit(
            GenerationRequest(
                request_type="video",
                prompt="cat",
                provider_params={"duration": 4},
                references=[ReferenceImage(blob_hash="hash")],
            )
        )


@pytest.mark.parametrize("cls", [OpenAIVideoProvider, VideoV1Provider])
def test_vendor_failure_is_preserved_and_secrets_removed(monkeypatch, cls):
    transport(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "status": "failed",
                "error": {
                    "message": "未获得最终结果；自动退还预扣额度 secret-for-test https://host/result?key=private"
                },
            },
        ),
    )
    p = cls(endpoint="https://video.example/v1", token="secret-for-test")
    r = p.poll(JobHandle(external_job_id="task-1"))
    assert r.status == "failed" and "自动退还预扣额度" in r.error
    assert "secret-for-test" not in r.error and "private" not in r.error


@pytest.mark.parametrize("cls", [OpenAIVideoProvider, VideoV1Provider])
@pytest.mark.parametrize("status", ["completed", "succeeded"])
def test_documented_success_states_download_existing_task(monkeypatch, cls, status):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.method == "GET"
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"video-content")
        return httpx.Response(200, json={"status": status})

    transport(monkeypatch, handler)
    p = cls(
        endpoint="https://video.example/v1",
        token="secret-for-test",
        config={"model": "video-model"},
    )
    result = p.poll(JobHandle(external_job_id="task-1"))
    assert result.status == "succeeded"
    assert result.outputs[0].data == b"video-content"
    assert calls == [("GET", "/v1/videos/task-1"), ("GET", "/v1/videos/task-1/content")]


def test_unknown_state_is_not_reported_as_terminal_failure(monkeypatch):
    transport(monkeypatch, lambda req: httpx.Response(200, json={"status": "new-state"}))
    with pytest.raises(ProviderError, match="继续查询原任务"):
        provider().poll(JobHandle(external_job_id="task-1"))


def test_model_config_requires_duration_and_no_unimplemented_references():
    args = dict(name="video", model="video", protocol="video_v1", modality="video")
    with pytest.raises(ValueError, match="时长选项"):
        ModelIn(**args)
    with pytest.raises(ValueError, match="文生视频"):
        ModelIn(**args, parameters={"duration": [4]}, max_reference_images=1)
    assert ModelIn(**args, parameters={"duration": [4]}).protocol == "video_v1"
