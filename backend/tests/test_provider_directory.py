"""Provider directory permissions and default clearing regressions."""

# ruff: noqa: F811
from app.models.generation import ProviderConfig
from app.modules.generation.provider_templates import BUILTINS, TemplateIn
from tests.test_creative_models import group
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup
from tests.test_platform_models import admin


def test_builtins_validate():
    for value in BUILTINS.values():
        TemplateIn.model_validate(value)


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
