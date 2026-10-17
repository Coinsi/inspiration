"""Removing connections preserves outputs, guards active jobs and clears routing defaults."""

# ruff: noqa: F811
import uuid

from sqlalchemy import select

from app.core.security import create_access_token
from app.models.generation import (
    CreativeModel,
    Generation,
    GenerationJob,
    ModelChannel,
    ProviderConfig,
)
from app.models.identity import User
from app.models.personal_model import PersonalModelService
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup
from tests.test_personal_models import connect


def job(db, ids, provider, status="succeeded", snapshot=None):
    row = GenerationJob(
        project_id=uuid.UUID(ids["project"]),
        target_type="shot",
        target_id=uuid.UUID(ids["shot"]),
        provider=provider,
        status=status,
        created_by=ids["user"],
        input_snapshot=snapshot or {},
    )
    db.add(row)
    db.flush()
    return row


def test_personal_remove_reconnect_and_preserve_history(studio):
    client, sessions, ids = studio
    service = connect(client)
    name = "personal_" + uuid.UUID(service["id"]).hex + "_seedream-4.5"
    with sessions() as db:
        history = job(db, ids, name)
        db.add(
            Generation(
                project_id=uuid.UUID(ids["project"]),
                job_id=history.id,
                target_type="shot",
                target_id=uuid.UUID(ids["shot"]),
                provider=name,
            )
        )
        db.commit()
    path = f"/api/v1/me/model-services/{service['id']}"
    assert client.delete(path + "?revision=999").status_code == 409
    assert client.delete(path + "?revision=1").status_code == 204
    assert client.get("/api/v1/me/model-services").json() == []
    with sessions() as db:
        row = db.get(PersonalModelService, uuid.UUID(service["id"]))
        assert not row.enabled and not row.credentials_encrypted and row.revision == 2
        assert db.scalar(select(Generation.id))
    assert (
        client.put(
            "/api/v1/me/model-services/ark", json={"preset_id": "ark", "models": ["seedream-4.5"]}
        ).status_code
        == 422
    )
    restored = connect(client)
    assert restored["revision"] == 3
    assert restored["models"][0]["verification"]["status"] == "unverified"


def test_personal_remove_ownership_and_active_job(studio):
    client, sessions, ids = studio
    service = connect(client)
    name = "personal_" + uuid.UUID(service["id"]).hex + "_seedream-4.5"
    path = f"/api/v1/me/model-services/{service['id']}?revision=1"
    with sessions() as db:
        job(db, ids, name, "running")
        other = User(
            username="stranger",
            email="stranger@example.com",
            display_name="Stranger",
            password_hash="unused",
        )
        db.add(other)
        db.flush()
        other_id = other.id
        db.commit()
    assert client.delete(path).status_code == 409
    client.headers["Authorization"] = "Bearer " + create_access_token(str(other_id))
    assert client.delete(path).status_code == 404


def test_project_remove_clears_models_routes_and_default(studio):
    client, sessions, ids = studio
    root, cid = setup(client, ids["project"])
    model = add(client, root, cid)
    with sessions() as db:
        row = CreativeModel(
            project_id=uuid.UUID(ids["project"]),
            name="Default image",
            modality="image",
            enabled=True,
            revision=1,
            definition={
                "is_default": True,
                "routes": [{"profile_id": model["id"], "enabled": True}],
            },
        )
        db.add(row)
        db.flush()
        creative_id = row.id
        job(db, ids, model["provider_name"])
        db.commit()
    assert client.delete(root + f"/model-channels/{cid}?revision=999").status_code == 409
    assert client.delete(root + f"/model-channels/{cid}?revision=1").status_code == 204
    assert client.get(root + "/model-channels").json()["channels"] == []
    with sessions() as db:
        assert db.get(ModelChannel, uuid.UUID(cid)) is None
        assert db.get(ProviderConfig, uuid.UUID(model["id"])) is None
        row = db.get(CreativeModel, creative_id)
        assert (
            not row.enabled and not row.definition["is_default"] and row.definition["routes"] == []
        )
        assert db.scalar(select(GenerationJob.id))


def test_project_remove_blocks_active_routed_job_and_unknown_channel(studio):
    client, sessions, ids = studio
    root, cid = setup(client, ids["project"])
    add(client, root, cid)
    with sessions() as db:
        job(db, ids, "routed_test", "submitted", {"routing": {"candidates": [{"channel_id": cid}]}})
        db.commit()
    assert client.delete(root + f"/model-channels/{cid}?revision=1").status_code == 409
    assert client.delete(root + f"/model-channels/{uuid.uuid4()}?revision=1").status_code == 404
    assert client.get(root + "/model-channels").json()["channels"][0]["has_token"]
