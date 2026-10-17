"""Creator defaults reuse existing profiles and never overwrite model routes."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import select

from app.models.generation import ModelChannel
from app.models.identity import Membership
from tests.test_creative_models import group
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup


@pytest.mark.parametrize("disabled_group", [True, False])
def test_default_text_profile_survives_disabled_group_or_route(studio, disabled_group):
    from app.modules.generation.channels import llm_config

    client, sessions, ids = studio
    root, channel = setup(client, ids["project"])
    current = add(
        client,
        root,
        channel,
        name="gpt-6-astra",
        model="gpt-6-astra",
        protocol="cloud_llm",
        modality="text",
        is_default=True,
    )
    routes = [{"profile_id": current["id"], "enabled": disabled_group}]
    if not disabled_group:
        other = add(client, root, channel, model="other", protocol="cloud_llm", modality="text")
        routes.append({"profile_id": other["id"]})
    old = group(
        client,
        root,
        [current],
        name="gpt-5.5",
        modality="text",
        enabled=not disabled_group,
        routes=routes,
    )
    providers = client.get(root + "/providers").json()
    profile = next(p for p in providers if p["id"] == current["id"])
    assert profile["enabled"] and not profile["config"].get("hidden_from_catalog")

    path = root + "/creative-models/select-default"
    body = {"id": current["id"], "source": "profile"}
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    selected = response.json()
    assert selected["id"] != old["id"]
    assert selected["name"] == "gpt-6-astra"
    assert selected["is_default"] and selected["available"]
    assert client.post(path, json=body).json()["id"] == selected["id"]
    models = client.get(root + "/creative-models").json()
    assert len(models) == 2
    previous = next(m for m in models if m["id"] == old["id"])
    assert previous["routes"] == old["routes"]
    assert previous["enabled"] == old["enabled"]
    assert previous["revision"] == old["revision"]
    with sessions() as db:
        assert llm_config(db, ids["project"]).config["model"] == "gpt-6-astra"
    providers = client.get(root + "/providers").json()
    assert next(p for p in providers if p["id"] == current["id"])["config"]["hidden_from_catalog"]


def test_choose_existing_profile_without_manual_directory_and_repeat(studio):
    client, _, ids = studio
    root, channel = setup(client, ids["project"])
    first = add(client, root, channel, protocol="mock", model="one")
    second = add(client, root, channel, protocol="mock", model="two")
    endpoint = root + "/creative-models/select-default"
    body = {"id": first["id"], "source": "profile"}
    response = client.post(endpoint, json=body)
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["is_default"] and created["routes"][0]["profile_id"] == first["id"]
    assert client.post(endpoint, json=body).json()["id"] == created["id"]
    response = client.post(endpoint, json={"id": second["id"], "source": "profile"})
    assert response.status_code == 200, response.text
    models = client.get(root + "/creative-models").json()
    assert len(models) == 2 and sum(m["is_default"] for m in models) == 1
    providers = client.get(root + "/providers").json()
    assert (
        sum(
            p["config"].get("is_default", False)
            for p in providers
            if p["provider_name"].startswith("creative_")
        )
        == 1
    )


def test_default_preserves_route_definition_prices_and_text_integration(studio):
    from app.modules.generation.channels import llm_config

    client, sessions, ids = studio
    root, channel = setup(client, ids["project"])
    a = add(client, root, channel, protocol="mock", model="image-one")
    b = add(client, root, channel, protocol="mock", model="image-two")
    existing = group(
        client, root, [a, b], price_policy="unified", price={"mode": "per_request", "amount": "4"}
    )
    response = client.post(
        root + "/creative-models/select-default", json={"id": existing["id"], "source": "model"}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["routes"] == existing["routes"] and result["price"] == existing["price"]
    text = add(client, root, channel, protocol="cloud_llm", modality="text", model="new-text")
    response = client.post(
        root + "/creative-models/select-default", json={"id": text["id"], "source": "profile"}
    )
    assert response.status_code == 200, response.text
    with sessions() as db:
        assert llm_config(db, ids["project"]).config["model"] == "new-text"
    assert client.get(root + "/creative-models").json()[0]["is_default"]  # Image unaffected.


def test_default_rejects_unavailable_foreign_and_viewer_without_partial_rows(studio):
    client, sessions, ids = studio
    root, channel = setup(client, ids["project"])
    item = add(client, root, channel, protocol="mock")
    with sessions() as db:
        db.get(ModelChannel, uuid.UUID(channel)).enabled = False
        db.commit()
    path = root + "/creative-models/select-default"
    body = {"id": item["id"], "source": "profile"}
    assert client.post(path, json=body).status_code == 422
    assert client.get(root + "/creative-models").json() == []
    assert client.post(path, json={**body, "id": str(uuid.uuid4())}).status_code == 404
    assert (
        client.post(
            f"/api/v1/projects/{ids['other']}/creative-models/select-default", json=body
        ).status_code
        == 403
    )
    with sessions() as db:
        db.scalar(select(Membership).where(Membership.project_id == ids["project"])).role = "viewer"
        db.commit()
    assert client.post(path, json=body).status_code == 403
