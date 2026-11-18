"""H3 V2 contracts and the shared settings-to-video regression."""

# ruff: noqa: F811
import io
import json
import urllib.request
import uuid

import httpx
import pytest

from app.adapters.contracts import GenerationRequest, JobHandle, ReferenceImage
from app.adapters.generation.minimax_h3 import MiniMaxH3VideoProvider
from app.core.errors import CapabilityUnsupported, ProviderError
from app.models.generation import GenerationJob, ProviderConfig
from app.modules.generation import media_engine, service
from app.modules.generation.minimax_models import H3_PROTOCOL, video_profile
from app.modules.generation.model_routing import SubmissionRejected
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_presets import adapter, transport
from tests.test_platform_models import ROOT, admin


def test_legacy_platform_profile_generates_a_playable_video(studio, monkeypatch, tmp_path):
    client, sessions, ids = studio
    admin(sessions, ids)
    endpoint = "https://api.minimax.cn/v1"
    channel = client.post(
        ROOT + "/model-channels",
        json={"name": "MiniMax", "endpoint": endpoint, "token": "fixture-token"},
    ).json()["id"]
    profile = client.post(
        f"{ROOT}/model-channels/{channel}/models",
        json={
            "name": "MiniMax-H3",
            "model": "MiniMax-H3",
            "protocol": "openai_video",
            "modality": "video",
            "max_video_seconds": 12,
        },
    ).json()
    assert profile["protocol"] == H3_PROTOCOL
    with sessions() as db:
        old = db.get(ProviderConfig, uuid.UUID(profile["id"]))
        old.config = {**old.config, "protocol": "openai_video", "param_schema": {}}
        db.commit()
        frozen = {"model_profile": old.config}
        previous = service._provider_instance(db, ids["project"], old.provider_name, frozen)
        assert previous.name == "openai_video"
    displayed = client.get(ROOT + "/model-channels").json()["channels"][0]["models"][0]
    assert displayed["id"] == profile["id"]
    assert displayed["protocol"] == H3_PROTOCOL and displayed["parameters"]["duration"][-1] == 12

    video = tmp_path / "test.mp4"
    media_engine.run(
        ["-f", "lavfi", "-i", "color=c=green:s=160x90:d=1", "-c:v", "libx264", str(video)]
    )

    def download(url, **_):
        assert url == "https://cdn.example.test/output.mp4"
        return io.BytesIO(video.read_bytes())

    monkeypatch.setattr(urllib.request, "urlopen", download)
    seen = []

    def upstream(request):
        seen.append((request.method, request.url.path))
        assert request.url.host == "api.minimax.cn"
        if request.method == "POST":
            assert json.loads(request.content) == {
                "model": "MiniMax-H3",
                "content": [{"type": "text", "text": "A plant in the breeze"}],
                "duration": 4,
                "resolution": "768P",
                "ratio": "16:9",
            }
            return httpx.Response(200, json={"task_id": "test-h3"})
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "test-h3",
                    "status": "succeeded",
                    "content": {"url": "https://cdn.example.test/output.mp4"},
                }
            },
        )

    transport(monkeypatch, upstream)
    root = f"/api/v1/projects/{ids['project']}"
    body = {
        "request_key": str(uuid.uuid4()),
        "provider": profile["provider_name"],
        "request_type": "video",
        "prompt_override": "A plant in the breeze",
        "use_references": False,
    }
    response = client.post(root + "/workbench/generate", json=body)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "succeeded", job.get("error")
    assert client.post(root + "/workbench/generate", json=body).json()["id"] == job["id"]
    with sessions() as db:
        saved = db.get(GenerationJob, uuid.UUID(job["id"]))
        assert (
            saved.external_job_id == "test-h3"
            and saved.input_snapshot["model_profile"]["protocol"] == H3_PROTOCOL
        )
    assert seen == [("POST", "/v2/video_generation"), ("GET", "/v2/query/video_generation/test-h3")]
    output = client.get(root + "/workbench/creations").json()[0]["outputs"][0]
    assert client.get(root + "/blobs/" + output["output_blob_hash"]).content == video.read_bytes()


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled", "unknown"])
def test_h3_receipts_only_poll(status, monkeypatch):
    def upstream(request):
        assert request.method == "GET" and request.url.path == "/v2/query/video_generation/saved"
        return httpx.Response(200, json={"task": {"id": "saved", "status": status}})

    transport(monkeypatch, upstream)
    provider = adapter(MiniMaxH3VideoProvider, "minimax", "minimax-h3")
    handle = JobHandle(external_job_id="saved")
    if status == "unknown":
        with pytest.raises(ProviderError):
            provider.poll(handle)
    else:
        assert provider.poll(handle).status == (
            "failed" if status in ("failed", "cancelled") else "running"
        )


def test_h3_frames_and_model_specific_parameters(monkeypatch):
    bodies = []

    def upstream(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"task_id": "h3"})

    transport(monkeypatch, upstream)
    provider = adapter(MiniMaxH3VideoProvider, "minimax", "minimax-h3-max")
    first = ReferenceImage(blob_hash="1", role="first_frame", data_url="data:image/png;base64,YQ==")
    last = first.model_copy(update={"blob_hash": "2", "role": "last_frame"})
    req = GenerationRequest(request_type="video", prompt="move", references=[first, last])
    for params in ({"duration": 4}, {"resolution": "2K"}, {"aspect_ratio": "16:9"}):
        with pytest.raises(CapabilityUnsupported):
            provider.submit(req.model_copy(update={"provider_params": params}))
    assert not bodies
    provider.submit(req)
    assert bodies[0]["ratio"] == "adaptive" and bodies[0]["duration"] == 5
    assert [v["role"] for v in bodies[0]["content"][1:]] == ["first_frame", "last_frame"]
    assert bodies[0]["content"][1]["image_url"] == {"url": first.data_url}
    provider.submit(req.model_copy(update={"references": [last]}))
    assert bodies[1]["content"][1]["role"] == "last_frame"


def test_h3_explicit_rejection_and_custom_gateway(monkeypatch):
    config = {"modality": "video", "model": "MiniMax-H3", "protocol": "openai_video"}
    assert video_profile(config, "https://proxy.example/v1") is config
    assert video_profile(config, "https://api.minimax.cn/custom/v1") is config
    provider = adapter(MiniMaxH3VideoProvider, "minimax", "minimax-h3")
    req = GenerationRequest(request_type="video", prompt="move")
    transport(monkeypatch, lambda _: httpx.Response(402, text="private upstream details"))
    with pytest.raises(SubmissionRejected, match="余额不足"):
        provider.submit(req)
