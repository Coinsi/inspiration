"""Shared models: administrator boundary, private credentials and real project execution."""

# ruff: noqa: F811
import json
import uuid

import pytest
from sqlalchemy import inspect

from alembic import command
from app.core.errors import CapabilityUnsupported
from app.models.generation import GenerationJob, ModelChannel
from app.models.identity import Membership, User
from app.modules.generation import channels, service
from tests.test_creative_models import generate, group
from tests.test_library_migrations import library_migration_schema  # noqa: F401
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup

ROOT = "/api/v1/admin/models"


def admin(sessions, ids, enabled=True):
    with sessions() as db:
        db.get(User, ids["user"]).is_platform_admin = enabled
        db.commit()


def shared(client, sessions, ids):
    admin(sessions, ids)
    r = client.post(
        ROOT + "/model-channels",
        json={
            "name": "平台共享",
            "endpoint": "https://shared.example/v1",
            "token": "private-platform-key",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_global_admin_boundary_and_private_catalog(studio):
    client, sessions, ids = studio
    assert client.get(ROOT + "/model-channels").status_code == 403
    assert client.get(ROOT + "/creative-models").status_code == 403
    cid = shared(client, sessions, ids)
    image = add(client, ROOT, cid, protocol="mock")
    model = group(client, ROOT, [image], is_default=True)
    project = f"/api/v1/projects/{ids['project']}"
    # Even a project administrator cannot rebind or change shared credentials.
    admin(sessions, ids, False)
    assert (
        client.put(
            ROOT + f"/model-channels/{cid}",
            json={"name": "attack", "endpoint": "https://attacker.example/v1", "revision": 1},
        ).status_code
        == 403
    )
    assert client.post(project + f"/model-channels/{cid}/discover").status_code == 404
    assert (
        client.post(
            project + "/creative-models/select-default",
            json={"id": image["id"], "source": "profile"},
        ).status_code
        == 404
    )
    assert (
        client.put(
            project + f"/creative-models/{model['id']}",
            json={
                "name": "attack",
                "modality": "image",
                "routes": [{"profile_id": image["id"]}],
                "revision": 1,
            },
        ).status_code
        == 404
    )
    data = client.get(project + "/providers")
    assert data.status_code == 200, data.text
    assert "private-platform-key" not in data.text and "shared.example" not in data.text
    visible = next(m for m in data.json() if m["provider_name"] == model["provider_name"])
    assert visible["config"]["scope"] == "platform" and visible["enabled"]
    assert visible["config"]["is_default"]
    # Ordinary project members can discover/use the same catalog, not manage it.
    with sessions() as db:
        membership = db.query(Membership).filter_by(user_id=ids["user"]).one()
        membership.role = "viewer"
        db.commit()
    assert client.get(project + "/providers").status_code == 200
    assert client.get(ROOT + "/model-channels").status_code == 403


def test_shared_runtime_default_vision_and_project_override(studio):
    client, sessions, ids = studio
    cid = shared(client, sessions, ids)
    text = add(
        client,
        ROOT,
        cid,
        protocol="cloud_llm",
        modality="text",
        model="shared-text",
        is_default=True,
        input_capabilities={"modalities": ["text", "image"], "max_images": 4},
    )
    logical = group(client, ROOT, [text], modality="text", is_default=True)
    with sessions() as db:
        pc = channels.llm_config(db, ids["project"])
        assert pc.provider_name == text["provider_name"]
        assert pc.config["input_capabilities"]["max_images"] == 4
        assert channels.selected_llm(db, ids["project"], logical["provider_name"]).id == pc.id
        assert channels.model_snapshot(db, ids["project"], text["provider_name"])["model_profile"]
        from app.modules.harness.kernel_modalities import resolve_cloud_input
        from tests.test_harness_context import context

        resolved = resolve_cloud_input(db, context(db, ids), logical["provider_name"])
        assert resolved["profile"]["kind"] == "platform"
        assert resolved["input_contract"]["max_images"] == 4
        resumed = resolve_cloud_input(
            db, context(db, ids), logical["provider_name"], preferred_profile=resolved["profile"]
        )
        assert resumed["profile"] == resolved["profile"]

    root, project_channel = setup(client, ids["project"])
    own = add(
        client,
        root,
        project_channel,
        protocol="cloud_llm",
        modality="text",
        model="project-text",
        is_default=True,
    )
    with sessions() as db:
        assert channels.llm_config(db, ids["project"]).provider_name == own["provider_name"]
        assert channels.llm_config(db, ids["other"]).provider_name == text["provider_name"]
        # Test endpoint must never borrow platform credentials for a user-supplied address.
        from unittest.mock import patch

        from app.core.config import settings

        with patch.object(settings, "llm_api_key", ""):
            ok, _ = service.test_provider(
                db,
                ids["project"],
                text["provider_name"],
                "llm",
                "https://attacker.example",
                None,
                {},
            )
            assert not ok


def test_shared_generation_stays_in_project_and_disabled_models_fail(studio):
    client, sessions, ids = studio
    cid = shared(client, sessions, ids)
    image = add(client, ROOT, cid, protocol="mock")
    model = group(client, ROOT, [image], is_default=True)
    admin(sessions, ids, False)
    root = f"/api/v1/projects/{ids['project']}"
    result = generate(client, root, ids, model)
    assert result["status"] == "succeeded", result
    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(result["id"]))
        assert str(job.project_id) == ids["project"] and job.created_by == ids["user"]
        assert "private-platform-key" not in json.dumps(job.input_snapshot)
        db.get(ModelChannel, uuid.UUID(cid)).enabled = False
        db.commit()
        with pytest.raises(CapabilityUnsupported):
            service._provider_instance(db, ids["project"], image["provider_name"])
    provider = next(
        m
        for m in client.get(root + "/providers").json()
        if m["provider_name"] == model["provider_name"]
    )
    assert not provider["enabled"]


def test_platform_schema_migration(library_migration_schema):
    connection, config, schema = library_migration_schema
    command.upgrade(config, "0055_conversation_attachments")
    command.upgrade(config, "0056_platform_models")
    for table in ("model_channel", "provider_config", "creative_model"):
        columns = inspect(connection).get_columns(table, schema=schema)
        assert next(c for c in columns if c["name"] == "project_id")["nullable"]
    command.downgrade(config, "0055_conversation_attachments")
    for table in ("model_channel", "provider_config", "creative_model"):
        columns = inspect(connection).get_columns(table, schema=schema)
        assert not next(c for c in columns if c["name"] == "project_id")["nullable"]


def test_platform_onboarding_defaults_idempotency_and_delete(studio):
    from tests.test_model_presets import payload

    client, sessions, ids = studio
    admin(sessions, ids)
    body = payload()
    response = client.post(ROOT + "/model-channels/connect", json=body)
    assert response.status_code == 200, response.text
    assert client.post(ROOT + "/model-channels/connect", json=body).json() == response.json()
    models = client.get(ROOT + "/creative-models").json()
    assert len(models) == 2 and all(m["is_default"] for m in models)
    channel = client.get(ROOT + "/model-channels").json()["channels"][0]
    extra = add(client, ROOT, channel["id"], protocol="mock", model="extra")
    selected = client.post(
        ROOT + "/creative-models/select-default", json={"id": extra["id"], "source": "profile"}
    )
    assert selected.status_code == 200, selected.text
    models = client.get(ROOT + "/creative-models").json()
    assert sum(m["is_default"] for m in models if m["modality"] == "image") == 1
    # Project channel lists never include the shared service's private connection settings.
    assert client.get(f"/api/v1/projects/{ids['project']}/model-channels").json()["channels"] == []
    removed = client.delete(
        ROOT + f"/model-channels/{channel['id']}?revision={channel['revision']}"
    )
    assert removed.status_code in (200, 204), removed.text
    assert client.get(ROOT + "/model-channels").json()["channels"] == []
