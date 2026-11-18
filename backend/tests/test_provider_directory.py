"""Provider directory permissions and default clearing regressions."""

# ruff: noqa: F811
import uuid

import pytest

from app.models.generation import ProviderConfig
from app.modules.generation.provider_templates import BUILTINS, TemplateIn
from tests.test_creative_models import group
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup
from tests.test_platform_models import admin


def test_builtins_validate():
    for value in BUILTINS.values():
        TemplateIn.model_validate(value)


@pytest.mark.parametrize("scope", ["project", "platform", "personal"])
def test_model_identity_is_scoped_to_connection_and_purpose(studio, scope):
    client, sessions, ids = studio
    admin(sessions, ids)
    base = {
        "project": f"/api/v1/projects/{ids['project']}/model-channels",
        "platform": "/api/v1/admin/models/model-channels",
        "personal": "/api/v1/me/model-channels",
    }[scope]
    connection = {"name": "连接一", "endpoint": "https://api.openai.com/v1", "token": "fixture"}
    channel = client.post(base, json=connection).json()["id"]
    path = f"{base}/{channel}/models"
    body = {"name": "别名", "model": "example", "modality": "image", "protocol": "gpt_image"}
    first = {**body, "request_id": str(uuid.uuid4())}
    saved = client.post(path, json=first).json()
    assert client.post(path, json=first).json()["id"] == saved["id"]
    assert (
        client.post(
            path, json={**body, "name": "另一个名字", "model": " example ", "protocol": "mock"}
        ).status_code
        == 409
    )
    other = client.post(path, json={**body, "model": "example-two"}).json()
    assert (
        client.put(
            f"{path}/{other['id']}", json={**body, "revision": other["revision"]}
        ).status_code
        == 409
    )
    assert (
        client.post(path, json={**body, "modality": "video", "protocol": "mock"}).status_code == 200
    )
    other_channel = client.post(base, json={**connection, "name": "连接二"}).json()["id"]
    assert client.post(f"{base}/{other_channel}/models", json=body).status_code == 200
    models = next(c["models"] for c in client.get(base).json()["channels"] if c["id"] == channel)
    assert len(models) == 3
    original = next(m for m in models if m["id"] == saved["id"])
    assert (
        client.put(
            f"{path}/{saved['id']}",
            json={**body, "name": "新名称", "revision": original["revision"]},
        ).status_code
        == 200
    )


def test_minimax_catalog_and_manual_entry_respect_output(studio, monkeypatch):
    import httpx

    from app.modules.generation import channels

    client, _, ids = studio
    base, channel = setup(client, ids["project"], provider_template_id="minimax")
    path = f"{base}/model-channels/{channel}"
    upstream = httpx.Client

    def models(request):
        assert request.method == "GET" and request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "MiniMax-M3"},
                    {"id": "MiniMax-H3"},
                    {"id": "MiniMax-H3-Image"},
                    {"id": "custom-id"},
                    {"id": "with-metadata", "architecture": {"output_modalities": ["video"]}},
                ]
            },
        )

    monkeypatch.setattr(
        channels.httpx, "Client", lambda **kw: upstream(transport=httpx.MockTransport(models), **kw)
    )
    assert client.post(path + "/discover?purpose=video").json()["models"] == [
        "MiniMax-H3",
        "with-metadata",
    ]
    assert client.post(path + "/discover?purpose=text").json()["models"] == ["MiniMax-M3"]
    assert client.post(path + "/discover?purpose=image").json()["models"] == ["MiniMax-H3-Image"]
    body = {"name": "测试", "model": "MiniMax-M3", "modality": "video", "protocol": "minimax_video"}
    assert client.post(path + "/models", json=body).status_code == 422
    assert client.post(path + "/models", json={**body, "model": "custom-id"}).status_code == 200
    assert (
        client.post(
            path + "/models", json={**body, "modality": "text", "protocol": "cloud_llm"}
        ).status_code
        == 200
    )


def test_existing_duplicates_keep_their_ids_and_can_be_edited(studio):
    client, sessions, ids = studio
    base, cid = setup(client, ids["project"])
    first = add(client, base, cid, model="same")
    second = add(client, base, cid, model="other")
    with sessions() as db:
        row = db.get(ProviderConfig, uuid.UUID(second["id"]))
        row.config = {**row.config, "model": "same"}
        db.commit()
    path = f"{base}/model-channels/{cid}/models"
    body = {"name": "旧路由", "model": "same", "modality": "image", "protocol": "gpt_image"}
    assert (
        client.put(
            f"{path}/{second['id']}", json={**body, "enabled": False, "revision": 1}
        ).status_code
        == 200
    )
    assert client.post(path, json=body).status_code == 409
    models = client.get(base + "/model-channels").json()["channels"][0]["models"]
    assert {m["id"] for m in models} == {first["id"], second["id"]}


def test_directory_edit_permissions_conflicts_and_hide(studio):
    client, sessions, ids = studio
    root = "/api/v1/admin/model-provider-templates"
    body = dict(BUILTINS["openai"], name="测试兼容网关")
    assert client.put(root + "/custom", json=body).status_code == 403
    admin(sessions, ids)
    response = client.put(root + "/custom", json=body)
    assert response.status_code == 200, response.text
    assert any(t["id"] == "custom" for t in client.get("/api/v1/model-provider-templates").json())
    assert client.put(root + "/custom", json=body).status_code == 409
    response = client.put(root + "/custom", json={**body, "revision": 1, "enabled": False})
    assert response.status_code == 200
    assert not any(
        t["id"] == "custom" for t in client.get("/api/v1/model-provider-templates").json()
    )
    assert (
        client.put(
            root + "/bad", json={**body, "routes": [{"modality": "video", "protocol": "unknown"}]}
        ).status_code
        == 422
    )
    assert (
        client.put(root + "/bad", json={**body, "key_url": "javascript:alert(1)"}).status_code
        == 422
    )


def test_clear_defaults_preserves_models_and_other_modalities(studio):
    client, sessions, ids = studio
    root, channel = setup(client, ids["project"])
    text = add(
        client, root, channel, model="text", protocol="cloud_llm", modality="text", is_default=True
    )
    image = add(client, root, channel, protocol="mock", model="image")
    group(client, root, [image], is_default=True)
    endpoint = root + "/creative-models/clear-default"
    assert client.post(endpoint, json={"modality": "text"}).status_code == 200
    assert client.post(endpoint, json={"modality": "text"}).status_code == 200
    with sessions() as db:
        p = db.get(ProviderConfig, text["id"])
        assert p.enabled and not p.config["is_default"]
    models = client.get(root + "/creative-models").json()
    assert models[0]["is_default"]
    assert client.post(endpoint, json={"modality": "image"}).status_code == 200
    models = client.get(root + "/creative-models").json()
    assert models[0]["enabled"] and not models[0]["is_default"]
    assert client.post(endpoint, json={"modality": "audio"}).status_code == 422


def test_template_connection_survives_custom_endpoint_and_old_editors(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/model-channels"
    body = {
        "name": "Custom Claude",
        "endpoint": "https://custom.example",
        "token": "test-only",
        "provider_template_id": "anthropic",
    }
    response = client.post(root, json=body)
    assert response.status_code == 200, response.text
    channel_id = response.json()["id"]
    saved = client.get(root).json()["channels"][0]
    assert saved["provider_template_id"] == "anthropic"
    body.pop("provider_template_id")
    body["revision"] = saved["revision"]
    assert client.put(root + "/" + channel_id, json=body).status_code == 200
    assert client.get(root).json()["channels"][0]["provider_template_id"] == "anthropic"


def test_native_catalog_protocols(monkeypatch):
    from types import SimpleNamespace

    import httpx

    from app.core.crypto import encrypt
    from app.modules.generation import channels

    real_client = httpx.Client
    seen = []

    def upstream(request):
        seen.append(request)
        if request.url.path == "/v1beta/models":
            assert request.headers["x-goog-api-key"] == "test-only"
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"models": [{"name": "models/gemini-test"}]})
        assert request.url.path == "/v1/models"
        assert request.headers["x-api-key"] == "test-only"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "claude-test"}]})

    monkeypatch.setattr(
        channels.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(upstream), **kw),
    )
    ch = SimpleNamespace(
        endpoint="https://example.invalid", credentials_encrypted=encrypt("test-only")
    )
    assert channels.discover_connection(ch, "gemini")["models"] == ["gemini-test"]
    assert channels.discover_connection(ch, "anthropic")["models"] == ["claude-test"]
    assert len(seen) == 2
