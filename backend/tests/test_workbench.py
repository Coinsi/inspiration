"""Workbench submissions use selected models, durable jobs and scoped references."""

# ruff: noqa: F811
import uuid

from sqlalchemy import func, select

from app.adapters.contracts import Capabilities
from app.adapters.generation.mock import MockProvider
from app.models.creation import Creation
from app.models.generation import Generation, GenerationJob, ModelChannel
from app.models.identity import Membership
from app.modules.agent import worker
from app.modules.agent.schemas import ModelAction
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup


def body(**changes):
    return {
        "request_key": str(uuid.uuid4()),
        "provider": "mock",
        "request_type": "image",
        "prompt_override": "清晨山间的云海",
        "provider_params": {},
        **changes,
    }


def test_media_results_persistence_idempotency_and_task_integration(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    request = body(provider_params={"aspect_ratio": "9:16"})
    estimated = client.post(root + "/workbench/estimate", json=request)
    assert estimated.status_code == 200, estimated.text
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Creation)) == 0
    response = client.post(root + "/workbench/generate", json=request)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "succeeded" and job["target_type"] == "creation"
    assert job["input_snapshot"]["provider_params"] == request["provider_params"]
    assert client.post(root + "/workbench/generate", json=request).json()["id"] == job["id"]
    assert (
        client.post(
            root + "/workbench/generate", json={**request, "prompt_override": "changed"}
        ).status_code
        == 409
    )
    history = client.get(root + "/workbench/creations").json()
    assert len(history) == 1 and len(history[0]["outputs"]) == 1
    image = history[0]["outputs"][0]
    assert image["prompt_snapshot"] == request["prompt_override"]
    assert client.get(root + f"/generations/{image['id']}").status_code == 200
    assert client.get(root + f"/blobs/{image['output_blob_hash']}").status_code == 200
    catalog = client.get(root + "/jobs/catalog").json()
    assert catalog["items"][0]["target_available"]
    assert catalog["items"][0]["target_name"] == request["prompt_override"]
    assert client.get(root + "/workbench/references").json()["items"][0]["id"] == image["id"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1


def test_reference_roles_ownership_and_invalid_model_rollback(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    first = client.post(root + "/workbench/generate", json=body()).json()
    image = client.get(root + "/workbench/creations").json()[0]["outputs"][0]
    req = body(references=[{"generation_id": image["id"], "role": "first_frame"}])
    assert client.post(root + "/workbench/generate", json=req).status_code == 422
    assert client.post(root + "/workbench/generate", json=body(provider="missing")).status_code in (
        404,
        422,
    )
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Creation)) == 1
    monkeypatch.setattr(
        MockProvider,
        "capabilities",
        lambda _: Capabilities(
            modalities={"image", "video"},
            features={"reference", "first_frame", "last_frame"},
            max_reference_images=2,
        ),
    )
    req = body(references=[{"generation_id": image["id"], "role": "reference"}])
    response = client.post(root + "/workbench/generate", json=req)
    assert response.status_code == 200, response.text
    snap = response.json()["input_snapshot"]
    assert snap["references"][0]["blob_hash"] == image["output_blob_hash"]
    assert snap["reference_sources"][0]["generation_id"] == image["id"]
    with sessions() as db:
        db.get(Generation, uuid.UUID(image["id"])).project_id = ids["other"]
        db.commit()
    assert (
        client.post(
            root + "/workbench/generate", json=body(references=req["references"])
        ).status_code
        == 404
    )
    assert (
        client.get(
            root.replace(str(ids["project"]), str(ids["other"])) + "/workbench/creations"
        ).status_code
        == 403
    )
    with sessions() as db:
        db.scalar(select(Membership).where(Membership.project_id == ids["project"])).role = "viewer"
        db.commit()
    assert client.post(root + "/workbench/generate", json=body()).status_code == 403
    assert first["status"] == "succeeded"


def test_explicit_text_choice_reaches_worker_without_changing_project_default(studio, monkeypatch):
    client, sessions, ids = studio
    root, channel = setup(client, ids["project"])
    add(
        client,
        root,
        channel,
        protocol="cloud_llm",
        modality="text",
        model="default-a",
        is_default=True,
    )
    chosen = add(client, root, channel, protocol="cloud_llm", modality="text", model="chosen-b")
    request = {
        "goal": "构思一个故事",
        "request_key": str(uuid.uuid4()),
        "engine": "cloud_llm",
        "model_provider": chosen["provider_name"],
    }
    response = client.post(root + "/agent/runs", json=request)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["model_provider"] == chosen["provider_name"]
    seen = []

    def propose(config, payload):
        seen.append(config["model"])
        return ModelAction(tool="finish", message="已完成故事构思")

    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "propose", propose)
    worker.advance(uuid.UUID(run["id"]))
    assert seen == ["chosen-b"]
    from app.modules.generation.channels import llm_config

    with sessions() as db:
        assert llm_config(db, ids["project"]).config["model"] == "default-a"
        db.get(ModelChannel, uuid.UUID(channel)).enabled = False
        db.commit()
    assert (
        client.post(
            root + "/agent/runs", json={**request, "request_key": str(uuid.uuid4())}
        ).status_code
        == 422
    )
    assert (
        client.post(
            root + "/agent/runs",
            json={**request, "request_key": str(uuid.uuid4()), "model_provider": "does-not-exist"},
        ).status_code
        == 404
    )


def test_video_uses_chosen_profile_parameters_and_quota_rollback(studio):
    client, sessions, ids = studio
    root, channel = setup(client, ids["project"])
    chosen = add(
        client,
        root,
        channel,
        name="Video B",
        protocol="mock",
        modality="video",
        model="video-b",
        parameters={"duration": [3, 5], "aspect_ratio": ["16:9", "9:16"], "resolution": ["720p"]},
    )
    request = body(
        provider=chosen["provider_name"],
        request_type="video",
        provider_params={"duration": 3, "aspect_ratio": "9:16", "resolution": "720p"},
    )
    result = client.post(root + "/workbench/generate", json=request)
    assert result.status_code == 200, result.text
    job = result.json()
    assert job["status"] == "succeeded", job["error"]
    assert job["provider"] == chosen["provider_name"]
    assert job["input_snapshot"]["model_profile"]["model"] == "video-b"
    assert job["input_snapshot"]["provider_params"] == request["provider_params"]
    output = client.get(root + "/workbench/creations").json()[0]["outputs"][0]
    assert output["output_type"] == "video"
    response = client.get(root + "/blobs/" + output["output_blob_hash"])
    assert response.content[4:8] == b"ftyp"
    from app.models.generation import Quota

    with sessions() as db:
        db.add(
            Quota(
                project_id=ids["project"],
                scope="project",
                limit_cost=1,
                used_cost=0,
                period="total",
            )
        )
        db.commit()
    assert client.post(root + "/workbench/generate", json=body()).status_code == 402
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Creation)) == 1
