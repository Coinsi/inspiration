"""H3 V2 contracts and the shared settings-to-video regression."""

# ruff: noqa: F811
import base64
import io
import json
import urllib.request
import uuid

import httpx
import pytest

from app.adapters.contracts import GenerationRequest, JobHandle, ReferenceImage
from app.adapters.generation.minimax_h3 import MiniMaxH3VideoProvider
from app.core.errors import CapabilityUnsupported, ProviderError
from app.models.billing import BillingSettings, CreditAccount
from app.models.generation import GenerationJob, ProviderConfig
from app.modules.billing import wallet
from app.modules.generation import media_engine, service
from app.modules.generation.minimax_models import H3_PROTOCOL, video_profile
from app.modules.generation.model_routing import SubmissionRejected
from tests.test_creative_models import group
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup
from tests.test_model_presets import adapter, transport
from tests.test_platform_models import ROOT, admin
from tests.test_workbench import body


@pytest.fixture
def result_video(tmp_path, monkeypatch):
    video = tmp_path / "test.mp4"
    media_engine.run(
        ["-f", "lavfi", "-i", "color=c=green:s=160x90:d=1", "-c:v", "libx264", str(video)]
    )
    data = video.read_bytes()

    def download(url, **_):
        assert url == "https://cdn.example.test/output.mp4"
        return io.BytesIO(data)

    monkeypatch.setattr(urllib.request, "urlopen", download)
    return data


def test_legacy_platform_profile_generates_a_playable_video(studio, monkeypatch, result_video):
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
    assert client.get(root + "/blobs/" + output["output_blob_hash"]).content == result_video


def test_library_frame_on_routed_h3_uses_image_ratio(studio, monkeypatch, result_video):
    client, sessions, ids = studio
    admin(sessions, ids)
    project = f"/api/v1/projects/{ids['project']}"
    image_job = client.post(project + "/workbench/generate", json=body()).json()
    assert image_job["status"] == "succeeded"
    image = client.get(project + "/workbench/creations").json()[0]["outputs"][0]
    image_bytes = client.get(project + "/blobs/" + image["output_blob_hash"]).content
    channel = client.post(
        ROOT + "/model-channels",
        json={"name": "MiniMax", "endpoint": "https://api.minimax.cn/v1", "token": "fixture"},
    ).json()["id"]
    profile = add(
        client,
        ROOT,
        channel,
        modality="video",
        protocol=H3_PROTOCOL,
        model="MiniMax-H3",
        max_reference_images=1,
    )
    routed = group(client, ROOT, [profile], modality="video")
    provider = routed["provider_name"]
    caps = client.get(f"{project}/providers/{provider}/capabilities").json()
    assert caps["aspect_ratio_from_image"] and "first_frame" in caps["features"]

    submitted = []

    def upstream(request):
        if request.method == "POST":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"task_id": "with-frame"})
        assert request.url.path == "/v2/query/video_generation/with-frame"
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "with-frame",
                    "status": "succeeded",
                    "content": {"url": "https://cdn.example.test/output.mp4"},
                }
            },
        )

    transport(monkeypatch, upstream)
    params = {"duration": 4, "resolution": "768P", "aspect_ratio": "16:9"}
    request = body(
        provider=provider,
        request_type="video",
        provider_params=params,
        references=[{"generation_id": image["id"], "role": "first_frame"}],
    )
    error = client.post(project + "/workbench/estimate", json={**request, "count": 2}).json()[
        "error"
    ]
    assert "数量设为 1" in error["message"]
    assert client.post(project + "/workbench/estimate", json=request).status_code == 200
    assert not submitted
    response = client.post(project + "/workbench/generate", json=request)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "succeeded", job.get("error")
    assert job["input_snapshot"]["provider_params"] == {"duration": 4, "resolution": "768P"}
    assert client.post(project + "/workbench/generate", json=request).json()["id"] == job["id"]
    assert len(submitted) == 1 and submitted[0]["ratio"] == "adaptive"
    sent_frame = submitted[0]["content"][1]
    assert sent_frame["role"] == "first_frame"
    assert base64.b64decode(sent_frame["image_url"]["url"].split(",")[1]) == image_bytes


def test_mixed_routes_keep_selectable_frame_ratio(studio):
    client, _, ids = studio
    root, channel = setup(client, ids["project"])
    h3 = add(
        client,
        root,
        channel,
        modality="video",
        protocol=H3_PROTOCOL,
        model="MiniMax-H3",
        max_reference_images=1,
    )
    fixed = add(
        client,
        root,
        channel,
        modality="video",
        protocol="ark_video",
        model="seedance",
        max_reference_images=1,
        parameters={"aspect_ratio": ["9:16"]},
    )
    model = group(client, root, [h3, fixed], modality="video")
    caps = client.get(f"{root}/providers/{model['provider_name']}/capabilities").json()
    assert not caps["aspect_ratio_from_image"]
    simulated = client.post(
        f"{root}/creative-models/{model['id']}/simulate",
        json={
            "request_type": "video",
            "prompt": "sunset",
            "references": [{"blob_hash": "ref", "role": "first_frame"}],
            "provider_params": {"aspect_ratio": "9:16"},
        },
    ).json()
    assert [c["profile_id"] for c in simulated["candidates"]] == [fixed["id"]]


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


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (1026, "input_moderation"),
        ("1027", "output_moderation"),
        ("9999", "provider_failure"),
        ("bad/private", "provider_failure"),
    ],
)
def test_h3_explains_verified_failure_without_echoing_raw_upstream_content(monkeypatch, code, kind):
    task = {"id": "original", "status": "failed", "error": {"code": code, "message": "PRIVATE"}}
    transport(monkeypatch, lambda _: httpx.Response(200, json={"task": task}))
    provider = adapter(MiniMaxH3VideoProvider, "minimax", "minimax-h3")
    handle = JobHandle(external_job_id="original")
    result = provider.poll(handle)
    assert result.status == "failed" and result.failure_kind == kind
    assert result.failure_code == (str(code) if str(code).isdigit() else None)
    assert "PRIVATE" not in result.error and "bad/private" not in result.error
    task["content"] = {"url": "https://cdn.example.test/video"}
    with pytest.raises(ProviderError, match="不一致"):
        provider.poll(handle)
    task["id"] = "different-task"
    with pytest.raises(ProviderError, match="不符"):
        provider.poll(handle)


def test_workbench_h3_moderation_releases_grant_credits(studio, monkeypatch):
    client, sessions, ids = studio
    admin(sessions, ids)
    root = f"/api/v1/projects/{ids['project']}"
    channel = client.post(
        ROOT + "/model-channels",
        json={"name": "MiniMax", "endpoint": "https://api.minimax.cn/v1", "token": "fixture"},
    ).json()["id"]
    profile = add(
        client,
        ROOT,
        channel,
        modality="video",
        protocol=H3_PROTOCOL,
        model="MiniMax-H3",
        max_video_seconds=12,
        price={"mode": "per_second", "amount": "10"},
    )
    model = group(client, ROOT, [profile], modality="video")
    with sessions.begin() as db:
        db.add(BillingSettings(id=1, payment_required=True))
        wallet.change(
            db, ids["user"], "test_funding", uuid.uuid4(), available=12000, grant_available=12000
        )
    requests = []

    def upstream(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "h3-original"})
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "h3-original",
                    "status": "failed",
                    "error": {"code": "1026", "message": "content blocked by moderation"},
                }
            },
        )

    transport(monkeypatch, upstream)
    data = body(
        provider=model["provider_name"],
        request_type="video",
        provider_params={"duration": 12, "resolution": "768P"},
    )
    response = client.post(root + "/workbench/generate", json=data)
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    detail = client.get(root + "/jobs/" + job_id).json()
    assert detail["status"] == "failed" and "输入未通过内容审核" in detail["error"]
    assert detail["credit_reservation"] == {"status": "released", "points": "120"}
    assert client.post(root + "/workbench/generate", json=data).json()["id"] == job_id
    assert requests == [
        ("POST", "/v2/video_generation"),
        ("GET", "/v2/query/video_generation/h3-original"),
    ]
    with sessions() as db:
        account = db.get(CreditAccount, ids["user"])
        assert (
            account.available,
            account.grant_available,
            account.reserved,
            account.grant_reserved,
        ) == (12000, 12000, 0, 0)


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
