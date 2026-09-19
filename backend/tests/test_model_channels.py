"""Channels share credentials, model selection is real and old identities survive."""

# ruff: noqa: F811
import io
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.crypto import decrypt
from app.models.generation import ModelChannel, ProviderConfig
from app.models.identity import Membership
from app.modules.generation import jobs, media_engine, service
from app.modules.narrative.service import _llm_config
from tests.test_media_workflow import studio  # noqa: F401


def setup(client, project, **values):
    root = f"/api/v1/projects/{project}"
    response = client.post(
        root + "/model-channels",
        json={
            "name": "同一服务",
            "endpoint": "https://models.example/v1",
            "token": "test-secret",
            **values,
        },
    )
    assert response.status_code == 200, response.text
    return root, response.json()["id"]


def add(client, root, cid, **values):
    response = client.post(
        root + f"/model-channels/{cid}/models",
        json={
            "name": "图片一",
            "model": "image-one",
            "protocol": "gpt_image",
            "modality": "image",
            **values,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_channel_credentials_defaults_conflicts_and_scope(studio):
    client, sessions, ids = studio
    root, cid = setup(client, ids["project"])
    first = add(client, root, cid)
    second = add(client, root, cid, name="图片二", model="image-two")
    add(
        client,
        root,
        cid,
        name="文字",
        model="text-one",
        protocol="cloud_llm",
        modality="text",
        is_default=True,
    )
    catalog = client.get(root + "/model-channels").json()
    assert len(catalog["channels"][0]["models"]) == 3
    assert "test-secret" not in json.dumps(catalog) and catalog["channels"][0]["has_token"]
    assert first["provider_name"] != second["provider_name"]
    _, second_channel = setup(client, ids["project"], name="另一渠道", token="second-secret")
    alternate = add(client, root, second_channel, model="image-one")
    add(
        client,
        root,
        second_channel,
        name="文字默认二",
        model="text-two",
        protocol="cloud_llm",
        modality="text",
        is_default=True,
    )
    with sessions() as db:
        assert _llm_config(db, ids["project"]).config["model"] == "text-two"
        provider = service._provider_instance(db, ids["project"], alternate["provider_name"])
        assert provider.token == "second-secret" and provider._model() == "image-one"
        for item in (first, second):
            provider = service._provider_instance(db, ids["project"], item["provider_name"])
            assert provider.token == "test-secret" and provider._model() == item["model"]
    edit = {"name": "新名称", "endpoint": "https://models.example/v1", "revision": 1}
    assert client.put(root + f"/model-channels/{cid}", json=edit).status_code == 200
    assert client.put(root + f"/model-channels/{cid}", json=edit).status_code == 409
    with sessions() as db:
        assert decrypt(db.get(ModelChannel, uuid.UUID(cid)).credentials_encrypted) == "test-secret"
    assert (
        client.put(
            root + f"/model-channels/{cid}", json={**edit, "revision": 2, "clear_token": True}
        ).status_code
        == 200
    )
    assert not client.get(root + "/model-channels").json()["channels"][0]["has_token"]
    assert (
        client.put(
            root + f"/model-channels/{second_channel}",
            json={
                "name": "无密钥渠道",
                "endpoint": "https://other.example/v1",
                "revision": 1,
                "clear_token": True,
            },
        ).status_code
        == 200
    )
    with sessions() as db:
        with pytest.raises(Exception, match="缺少地址或密钥"):
            _llm_config(db, ids["project"])
        from app.modules.assist.service import _engine

        assert _engine(db, ids["project"], "mock").name == "mock"
        for item in db.scalars(select(ProviderConfig).where(ProviderConfig.kind == "llm")):
            item.enabled = False
        db.commit()
        with pytest.raises(Exception, match="已停用"):
            _llm_config(db, ids["project"])
    assert client.get(f"/api/v1/projects/{ids['other']}/model-channels").status_code == 403
    assert (
        client.post(
            root + f"/model-channels/{uuid.uuid4()}/models",
            json={"name": "x", "model": "x", "protocol": "mock", "modality": "image"},
        ).status_code
        == 404
    )
    with sessions() as db:
        member = db.scalar(select(Membership).where(Membership.project_id == ids["project"]))
        member.role = "viewer"
        db.commit()
    assert client.get(root + "/model-channels").status_code == 403
    assert client.put(root + f"/model-channels/{cid}", json=edit).status_code == 403
    assert "test-secret" not in client.get(root + "/providers").text


def test_model_update_validation_disable_and_legacy_import(studio):
    client, sessions, ids = studio
    root, cid = setup(client, ids["project"])
    model = add(client, root, cid, protocol="mock", model="mock-image")
    bad = {"name": "x", "model": "x", "protocol": "cloud_llm", "modality": "video"}
    assert client.post(root + f"/model-channels/{cid}/models", json=bad).status_code == 422
    assert (
        client.post(
            root + f"/model-channels/{cid}/models",
            json={**bad, "protocol": "openai_video", "parameters": {"model": ["evil"]}},
        ).status_code
        == 422
    )
    alias = model["provider_name"]
    assert (
        client.put(root + "/providers", json={"provider_name": alias, "config": {}}).status_code
        == 409
    )
    assert (
        client.put(
            root + "/providers", json={"provider_name": "fake", "config": {"channel_id": cid}}
        ).status_code
        == 409
    )
    body = {k: v for k, v in model.items() if k not in ("id", "provider_name")}
    assert (
        client.put(
            root + f"/model-channels/{cid}/models/{model['id']}", json={**body, "enabled": False}
        ).status_code
        == 200
    )
    response = client.post(root + f"/shots/{ids['shot']}/generate", json={"provider": alias})
    assert response.status_code == 422, response.text
    assert (
        client.put(root + f"/model-channels/{cid}/models/{model['id']}", json=body).status_code
        == 409
    )
    assert (
        client.put(
            root + "/providers",
            json={
                "provider_name": "gpt_image",
                "endpoint": "https://old.example/v1",
                "token": "old-key",
                "config": {"model": "old-model"},
            },
        ).status_code
        == 200
    )
    assert client.post(root + "/model-channels/import-legacy").json()["imported"] == 1
    assert client.post(root + "/model-channels/import-legacy").json()["imported"] == 0
    with sessions() as db:
        provider = service._provider_instance(db, ids["project"], "gpt_image")
        assert provider._model() == "old-model" and provider.token == "old-key"
        assert provider.endpoint == "https://old.example/v1"
    request_id = str(uuid.uuid4())
    a = client.post(
        root + "/model-channels", json={"name": "重复创建", "request_id": request_id}
    ).json()
    b = client.post(
        root + "/model-channels", json={"name": "重复创建", "request_id": request_id}
    ).json()
    assert a == b


def test_real_image_request_selected_model_and_frozen_profile(studio, monkeypatch):
    import base64
    import urllib.request

    client, sessions, ids = studio
    root, cid = setup(client, ids["project"])
    model = add(client, root, cid, parameters={"size": ["1024x1024"]})
    requests = []

    def respond(req, **kwargs):
        requests.append((req.full_url, json.loads(req.data), req.get_header("Authorization")))
        return io.BytesIO(
            json.dumps(
                {"data": [{"b64_json": base64.b64encode(media_engine.mock_image()).decode()}]}
            ).encode()
        )

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    r = client.post(
        root + f"/shots/{ids['shot']}/generate",
        json={
            "provider": model["provider_name"],
            "count": 1,
            "prompt_override": "test",
            "provider_params": {"size": "1024x1024"},
        },
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert "test-secret" not in json.dumps(job)
    body = {k: v for k, v in model.items() if k not in ("id", "provider_name")}
    body["model"] = "changed-after-submit"
    assert (
        client.put(root + f"/model-channels/{cid}/models/{model['id']}", json=body).status_code
        == 200
    )
    with sessions() as db:
        jobs.execute(db, uuid.UUID(job["id"]))
    assert client.get(root + f"/jobs/{job['id']}").json()["status"] == "succeeded"
    assert requests[0][1]["model"] == "image-one" and requests[0][2] == "Bearer test-secret"
    assert requests[0][1]["size"] == "1024x1024"
    invalid = client.post(
        root + f"/shots/{ids['shot']}/generate",
        json={
            "provider": model["provider_name"],
            "count": 1,
            "provider_params": {"model": "unapproved"},
        },
    )
    assert invalid.status_code == 422
    assert (
        client.put(
            root + f"/model-channels/{cid}",
            json={
                "name": "stop",
                "endpoint": "https://models.example/v1",
                "enabled": False,
                "revision": 1,
            },
        ).status_code
        == 200
    )
    assert client.get(root + f"/providers/{model['provider_name']}/capabilities").status_code == 422
    assert not client.get(root + "/providers").json()[0]["enabled"]


def test_video_protocol_and_discovery_without_secret_echo(studio, monkeypatch):
    from app.adapters.contracts import GenerationRequest

    client, sessions, ids = studio
    root, cid = setup(client, ids["project"])
    model = add(
        client,
        root,
        cid,
        protocol="openai_video",
        modality="video",
        model="video-one",
        parameters={"duration": [4, 8], "size": ["1280x720"]},
    )
    requests = []

    def handle(req):
        requests.append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(
                200, json={"data": [{"id": "video-one"}, {"id": "image-two"}, {"id": "video-one"}]}
            )
        if req.method == "POST":
            return httpx.Response(200, json={"id": "task-123"})
        if req.url.path.endswith("/content"):
            return httpx.Response(200, content=b"actual-upstream-bytes")
        return httpx.Response(200, json={"status": "completed"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    response = client.post(root + f"/model-channels/{cid}/discover")
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["image-two", "video-one"]
    with sessions() as db:
        provider = service._provider_instance(db, ids["project"], model["provider_name"])
        req = GenerationRequest(
            request_type="video", prompt="pan", provider_params={"duration": 4, "size": "1280x720"}
        )
        result = provider.poll(provider.submit(req))
        assert result.outputs[0].data == b"actual-upstream-bytes"
        assert result.outputs[0].meta["model"] == "video-one"
        with pytest.raises(Exception, match="数量设为1"):
            provider.estimate_cost(req.model_copy(update={"count": 2}))
    post = next(r for r in requests if r.method == "POST")
    assert b"video-one" in post.content and b'name="seconds"' in post.content
    assert post.headers["authorization"] == "Bearer test-secret"
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda r: httpx.Response(401, text="test-secret")),
            **kwargs,
        ),
    )
    error = client.post(root + f"/model-channels/{cid}/discover")
    assert error.status_code == 502 and "test-secret" not in error.text
