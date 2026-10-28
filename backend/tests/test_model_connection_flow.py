"""Connection checks don't save keys or generate; bulk model addition is atomic and retryable."""

# ruff: noqa: F811
import uuid

import httpx
from sqlalchemy import func, select

from app.models.generation import ModelChannel, ProviderConfig
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import setup
from tests.test_platform_models import ROOT, admin


def test_unsaved_check_and_saved_scope_without_generation(studio, monkeypatch):
    client, sessions, ids = studio
    from app.modules.generation import channels

    real_client = httpx.Client
    seen = []

    def upstream(request):
        seen.append(request)
        assert request.method == "GET" and request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "model-a"}]})

    monkeypatch.setattr(
        channels.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs),
    )
    root = f"/api/v1/projects/{ids['project']}"
    body = {"name": "test", "endpoint": "https://example.invalid/v1", "token": "test-only"}
    result = client.post(root + "/model-channels/check", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["model_count"] == 1 and "尚未验证" in result.json()["message"]
    assert "test-only" not in result.text
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(ModelChannel)) == 0
    _, cid = setup(client, ids["project"])
    assert (
        client.post(
            root + f"/model-channels/check?channel_id={cid}", json={**body, "token": None}
        ).status_code
        == 200
    )
    assert seen[-1].headers["Authorization"] == "Bearer test-secret"
    admin(sessions, ids)
    assert (
        client.post(ROOT + f"/model-channels/check?channel_id={cid}", json=body).status_code == 404
    )
    assert len(seen) == 2
    assert client.post(ROOT + "/model-channels/check", json=body).status_code == 200


def test_batch_is_atomic_idempotent_and_scoped(studio):
    client, sessions, ids = studio
    root, cid = setup(client, ids["project"])
    item = {
        "name": "Text",
        "model": "text-id",
        "modality": "text",
        "protocol": "cloud_llm",
        "request_id": str(uuid.uuid4()),
    }
    image = {
        "name": "Image",
        "model": "image-id",
        "modality": "image",
        "protocol": "gpt_image",
        "request_id": str(uuid.uuid4()),
    }
    path = root + f"/model-channels/{cid}/models/batch"
    result = client.post(path, json={"models": [item, image]})
    assert result.status_code == 200, result.text
    assert client.post(path, json={"models": [item, image]}).json() == result.json()
    invalid = client.post(
        path,
        json={
            "models": [{**item, "request_id": str(uuid.uuid4())}, {**image, "modality": "video"}]
        },
    )
    assert invalid.status_code == 422
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(ProviderConfig)) == 2
    admin(sessions, ids)
    assert (
        client.post(
            ROOT + f"/model-channels/{cid}/models/batch", json={"models": [item]}
        ).status_code
        == 404
    )
