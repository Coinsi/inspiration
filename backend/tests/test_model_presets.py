"""Onboarding is atomic, repeatable, scoped and does not call paid upstream APIs."""

# ruff: noqa: F811
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.adapters.contracts import GenerationRequest, JobHandle, ReferenceImage
from app.adapters.generation.native_media import (
    ArkImageProvider,
    ArkVideoProvider,
    MiniMaxVideoProvider,
    NativeMedia,
)
from app.core.errors import CapabilityUnsupported, ProviderError
from app.models.generation import ModelChannel, ProviderConfig
from app.models.identity import Membership
from app.modules.generation import channels, presets
from app.modules.generation.model_routing import SubmissionRejected
from tests.test_media_workflow import studio  # noqa: F401


def payload(**extra):
    return {
        "preset_id": "ark",
        "token": "onboarding-secret",
        "models": ["seedream-4.5", "seedance-1.5-pro"],
        "request_id": str(uuid.uuid4()),
        **extra,
    }


def test_connect_creates_ready_directory_preserves_defaults_and_deduplicates(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    monkeypatch.setattr(
        NativeMedia,
        "_request",
        lambda *_a, **_kw: pytest.fail("Onboarding must not contact upstream"),
    )
    # ASGI TestClient uses its own request method.
    body = payload()
    result = client.post(root + "/model-channels/connect", json=body)
    assert result.status_code == 200, result.text
    data = result.json()
    assert len(data["models"]) == 2
    assert all(m["available"] and m["is_default"] for m in data["models"])
    for attempt in [body, {**body, "request_id": str(uuid.uuid4())}]:
        repeat = client.post(root + "/model-channels/connect", json=attempt)
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["channel_id"] == data["channel_id"]
    public = client.get(root + "/model-channels").json()
    assert len(public["channels"]) == 1 and len(public["channels"][0]["models"]) == 2
    assert "onboarding-secret" not in json.dumps(public)
    # A second key becomes another route to the same explicit built-in identity.
    second = client.post(root + "/model-channels/connect", json=payload(token="backup-secret"))
    assert second.status_code == 200, second.text
    models = client.get(root + "/creative-models").json()
    assert len(models) == 2 and all(m["route_count"] == 2 for m in models)
    assert {m["id"] for m in models} == {m["id"] for m in data["models"]}
    assert (
        client.post(root + "/model-channels/connect", json={**body, "token": "changed"}).status_code
        == 409
    )
    assert (
        client.post(
            root + "/model-channels/connect", json=payload(endpoint="https://host/?key=secret")
        ).status_code
        == 422
    )
    assert client.get(f"/api/v1/projects/{ids['other']}/model-channels/presets").status_code == 403
    with sessions() as db:
        db.scalar(select(Membership).where(Membership.project_id == ids["project"])).role = "viewer"
        db.commit()
    assert client.post(root + "/model-channels/connect", json=payload()).status_code == 403


def test_connect_rollback_and_simulation_validates_native_combinations(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    original = channels.save_model

    def broken(db, ctx, cid, data, model_id=None):
        if data.modality == "video":
            raise CapabilityUnsupported("test rollback")
        return original(db, ctx, cid, data, model_id)

    monkeypatch.setattr(channels, "save_model", broken)
    assert client.post(root + "/model-channels/connect", json=payload()).status_code == 422
    with sessions() as db:
        assert not db.scalar(select(ModelChannel)) and not db.scalar(select(ProviderConfig))
    monkeypatch.setattr(channels, "save_model", original)
    result = client.post(
        root + "/model-channels/connect", json=payload(preset_id="minimax", models=["hailuo-2.3"])
    )
    assert result.status_code == 200, result.text
    ident = result.json()["models"][0]["id"]
    path = root + f"/creative-models/{ident}/simulate"
    invalid = {
        "request_type": "video",
        "prompt": "test",
        "provider_params": {"duration": 10, "resolution": "1080P"},
    }
    assert client.post(path, json=invalid).status_code == 422
    invalid["provider_params"]["duration"] = 6
    assert client.post(path, json=invalid).status_code == 200


def adapter(cls, preset, key):
    config = presets.PRESETS[preset]["models"][key]
    config = {**config, "param_schema": {k: {"enum": v} for k, v in config["parameters"].items()}}
    return cls(endpoint="https://upstream.example/v1", token="never-leak", config=config)


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )


def test_ark_json_image_edit_and_video_frames_contract(monkeypatch):
    calls = []

    def handler(req):
        assert req.headers["authorization"] == "Bearer never-leak"
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/image.png"}]})
        if req.method == "POST":
            return httpx.Response(200, json={"id": "task-1"})
        return httpx.Response(
            200,
            json={"status": "succeeded", "content": {"video_url": "https://cdn.example/video.mp4"}},
        )

    transport(monkeypatch, handler)
    ref = ReferenceImage(blob_hash="abc", data_url="data:image/png;base64,aGVsbG8=")
    image = adapter(ArkImageProvider, "ark", "seedream-4.5")
    handle = image.submit(
        GenerationRequest(
            request_type="image", prompt="edit", references=[ref], provider_params={"size": "2K"}
        )
    )
    assert image.poll(handle).outputs[0].type == "image"
    body = calls[0][2]
    assert body["image"] == [ref.data_url] and body["sequential_image_generation"] == "disabled"
    assert "n" not in body and "quality" not in body
    video = adapter(ArkVideoProvider, "ark", "seedance-1.5-pro")
    req = GenerationRequest(
        request_type="video",
        prompt="move",
        references=[
            ref.model_copy(update={"role": "first_frame"}),
            ref.model_copy(update={"role": "last_frame"}),
        ],
        provider_params={"duration": 5, "resolution": "720p", "aspect_ratio": "16:9"},
    )
    handle = video.submit(req)
    assert video.poll(JobHandle(external_job_id=handle.external_job_id)).outputs[0].type == "video"
    assert calls[1][2]["ratio"] == "16:9" and calls[1][2]["content"][2]["role"] == "last_frame"
    assert calls[-1][1].endswith("/tasks/task-1")
    with pytest.raises(CapabilityUnsupported):
        video.validate_request(req.model_copy(update={"references": [ref, ref]}))


def test_minimax_poll_and_file_retrieval_and_preflight(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        if req.method == "POST":
            return httpx.Response(200, json={"task_id": "task-2", "base_resp": {"status_code": 0}})
        if req.url.path.endswith("/query/video_generation"):
            assert req.url.params["task_id"] == "task-2"
            return httpx.Response(
                200,
                json={"status": "Success", "file_id": "file-2", "base_resp": {"status_code": 0}},
            )
        assert req.url.path.endswith("/files/retrieve") and req.url.params["file_id"] == "file-2"
        return httpx.Response(
            200,
            json={
                "file": {"download_url": "https://cdn.example/video.mp4"},
                "base_resp": {"status_code": 0},
            },
        )

    transport(monkeypatch, handler)
    p = adapter(MiniMaxVideoProvider, "minimax", "hailuo-2.3")
    req = GenerationRequest(
        request_type="video", prompt="move", provider_params={"duration": 10, "resolution": "1080P"}
    )
    with pytest.raises(CapabilityUnsupported, match="6 秒"):
        p.submit(req)
    assert not calls
    req.provider_params["duration"] = 6
    handle = p.submit(req)
    assert p.poll(handle).status == "succeeded"
    assert len(calls) == 3 and sum(c.method == "POST" for c in calls) == 1


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, SubmissionRejected),
        (429, SubmissionRejected),
        (500, ProviderError),
        (307, ProviderError),
    ],
)
def test_native_http_errors_and_redirects_do_not_echo_secrets(monkeypatch, status, expected):
    transport(
        monkeypatch,
        lambda req: httpx.Response(
            status, headers={"Location": "https://other.example"}, text="never-leak"
        ),
    )
    p = adapter(ArkVideoProvider, "ark", "seedance-1.5-pro")
    with pytest.raises(expected) as error:
        p.submit(GenerationRequest(request_type="video", prompt="move"))
    assert "never-leak" not in str(error.value)
    if status not in (401, 429):
        assert not isinstance(error.value, SubmissionRejected)
