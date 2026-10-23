"""Actual private/project catalog -> JSON decision -> local protocol estimate.

These tests use isolated PostgreSQL schemas and real provider capability and
pricing adapters. No model/network generation is submitted.
"""

# ruff: noqa: F811
import json
import uuid

import pytest
from sqlalchemy import func, select

from app.adapters.assist import cloud_llm
from app.adapters.generation.gpt_image import GptImageProvider
from app.core.crypto import encrypt
from app.models.generation import GenerationJob, ProviderConfig
from app.models.harness import ProductionRun
from app.models.identity import Membership, User
from app.models.personal_model import PersonalModelService
from app.modules.harness import runtime, schemas, service
from app.modules.tools import capabilities
from app.modules.tools.contracts import ToolContext
from app.modules.tools.invocation import invoke
from tests.test_creative_models import group
from tests.test_harness_context import context
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup


def configured(studio):
    client, _, ids = studio
    root, channel = setup(client, ids["project"])
    image = add(
        client,
        root,
        channel,
        protocol="gpt_image",
        model="image-real-protocol",
        parameters={"size": ["1024x1024"], "quality": ["low"]},
        max_reference_images=2,
        price={"mode": "per_request", "amount": "7"},
    )
    logical = group(client, root, [image], is_default=True)
    text = add(
        client,
        root,
        channel,
        protocol="cloud_llm",
        model="text-real-protocol",
        modality="text",
        input_capabilities={"modalities": ["text", "image"], "max_images": 3},
    )
    return root, image, logical, text


def test_catalog_uses_composer_routes_defaults_and_private_owner_without_secrets(studio):
    client, sessions, ids = studio
    root, backing, logical, text = configured(studio)
    personal = client.put(
        "/api/v1/me/model-services/ark",
        json={"preset_id": "ark", "token": "personal-test-secret", "models": ["seedream-4.5"]},
    )
    assert personal.status_code == 200, personal.text
    own_name = "personal_" + uuid.UUID(personal.json()["id"]).hex + "_seedream-4.5"
    with sessions() as db:
        stranger = User(
            username="other-model-owner",
            email="models@example.invalid",
            password_hash="x",
            display_name="Other owner",
        )
        db.add(stranger)
        db.flush()
        own = db.get(PersonalModelService, uuid.UUID(personal.json()["id"]))
        other = PersonalModelService(
            user_id=stranger.id,
            name="Private stranger service",
            preset_id=own.preset_id,
            endpoint="https://secret-endpoint.invalid/v1",
            credentials_encrypted=encrypt("stranger-private-key"),
            models=own.models,
        )
        db.add(other)
        # Bad declared media type must not enter image selection just because
        # an image adapter happens to be configured behind it.
        db.add(
            ProviderConfig(
                project_id=uuid.UUID(ids["project"]),
                provider_name="bad-video",
                kind="generation",
                endpoint="https://secret-endpoint.invalid/v1",
                credentials_encrypted=encrypt("hidden-key"),
                config={"modality": "video", "protocol": "gpt_image", "model": "image-only"},
            )
        )
        db.add(
            ProviderConfig(
                project_id=uuid.UUID(str(ids["other"])),
                provider_name="foreign-project",
                kind="generation",
                config={"modality": "image", "protocol": "mock"},
            )
        )
        db.commit()
        result = invoke(db, context(db, ids), ToolContext(), "media.models", {})
        assert result.status == "succeeded", result.model_dump()
        value = result.data
        providers = {p["provider"]: p for p in value["models"]}
        assert set(providers) == {logical["provider_name"], text["provider_name"], own_name}
        assert backing["provider_name"] not in providers
        assert value["defaults"]["image"] == logical["provider_name"]
        assert value["suggested"]["image"] == logical["provider_name"]
        actual = providers[logical["provider_name"]]
        assert actual["capabilities"]["modalities"] == ["image"]
        assert actual["capabilities"]["strict_parameters"]
        assert actual["capabilities"]["max_reference_images"] == 2
        assert actual["param_schema"]["size"]["enum"] == ["1024x1024"]
        assert providers[text["provider_name"]]["input_capabilities"]["max_images"] == 3
        assert providers[own_name]["scope"] == "personal"
        assert [p["provider"] for p in value["unavailable"]] == ["bad-video"]
        serialized = json.dumps(value)
        for secret in (
            "test-secret",
            "personal-test-secret",
            "stranger-private-key",
            "hidden-key",
            "secret-endpoint",
            "endpoint",
            "credentials",
            "Private stranger service",
        ):
            assert secret not in serialized
        page = invoke(
            db, context(db, ids), ToolContext(), "media.models", {"modality": "image", "limit": 1}
        )
        assert page.data["total"] == 2 and page.data["next_offset"] == 1
        assert all(p["modality"] == "image" for p in page.data["models"])
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
    front = client.get(root + "/providers").json()
    assert own_name in [p["provider_name"] for p in front]


def test_directory_revalidates_membership_and_disallows_caller_owner_selection(studio):
    _, sessions, ids = studio
    with sessions() as db:
        saved = context(db, ids)
        denied = invoke(db, saved, ToolContext(), "media.models", {"user_id": str(uuid.uuid4())})
        assert denied.status == "failed"
        member = db.scalar(select(Membership).where(Membership.project_id == ids["project"]))
        db.delete(member)
        db.commit()
        denied = invoke(db, saved, ToolContext(), "media.models", {})
        assert denied.status == "failed" and denied.error.category == "authorization"


def test_video_catalog_matches_frontend_capabilities_and_excludes_disabled_models(studio):
    client, sessions, ids = studio
    root, channel = setup(client, ids["project"])
    video = add(
        client,
        root,
        channel,
        protocol="video_v1",
        model="configured-video",
        modality="video",
        parameters={"duration": [5, 10], "resolution": ["720p"]},
        max_reference_images=2,
        max_reference_videos=1,
    )
    disabled = add(
        client,
        root,
        channel,
        protocol="gpt_image",
        model="disabled-image",
        enabled=False,
    )
    expected = client.get(root + f"/providers/{video['provider_name']}/capabilities")
    assert expected.status_code == 200, expected.text
    with sessions() as db:
        result = invoke(db, context(db, ids), ToolContext(), "media.models", {})
        assert result.status == "succeeded", result.model_dump()
        assert [m["provider"] for m in result.data["models"]] == [video["provider_name"]]
        actual = result.data["models"][0]
        assert actual["modality"] == "video"
        # All reference-origin restrictions and strict protocol parameter limits
        # are inherited from the same resolver that the front end uses.
        assert actual["capabilities"] == expected.json()
        assert actual["param_schema"]["duration"]["enum"] == [5, 10]
        assert result.data["defaults"]["video"] is None
        assert result.data["suggested"]["video"] == video["provider_name"]
        assert disabled["provider_name"] not in json.dumps(result.data)
        estimate = invoke(
            db,
            context(db, ids),
            ToolContext(),
            "media.estimate",
            {
                "provider": actual["provider"],
                "prompt": "夜雨",
                "request_type": "video",
                "provider_params": {"duration": 999, "resolution": "720p"},
            },
        )
        assert estimate.status == "failed" and estimate.error.category == "capability"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_cloud_kernel_discovers_models_and_estimates_real_protocol_without_generation(
    studio, monkeypatch
):
    _, sessions, ids = studio
    _, _, logical, text = configured(studio)
    calls = []

    def no_generation(*_):
        pytest.fail("Read-only discovery and estimation submitted a paid generation")

    monkeypatch.setattr(GptImageProvider, "submit", no_generation)

    def text_response(system, content, base_url, api_key, model, **kwargs):
        assert "media.models" in system and model == "text-real-protocol"
        packet = json.loads(content[0]["text"])
        assert packet["work_id"] is None
        calls.append(packet)
        steps = packet["steps"]
        if not steps:
            action = {
                "kind": "tool",
                "tool": "capabilities.discover",
                "arguments": {"names": ["media.models", "media.estimate"]},
            }
        elif len(steps) == 1:
            assert {t["name"] for t in packet["tools"]} >= {"media.models", "media.estimate"}
            action = {"kind": "tool", "tool": "media.models", "arguments": {"modality": "image"}}
        elif len(steps) == 2:
            directory = steps[-1]["receipt"]["data"]
            assert directory["suggested"]["image"] == logical["provider_name"]
            action = {
                "kind": "tool",
                "tool": "media.estimate",
                "arguments": {
                    "provider": directory["suggested"]["image"],
                    "prompt": "雨夜街道",
                    "request_type": "image",
                    "provider_params": {"size": "1024x1024", "quality": "low"},
                },
            }
        else:
            assert steps[-1]["receipt"]["status"] == "succeeded"
            assert steps[-1]["receipt"]["data"]["estimated_points"] == 7
            assert not steps[-1]["receipt"]["data"]["submitted"]
            action = {"kind": "finish", "message": "只完成模型能力查询和估价，未开始生成。"}
        return json.dumps(action), {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

    monkeypatch.setattr(cloud_llm, "_chat", text_response)
    with sessions() as db:
        run = service.create(
            db,
            context(db, ids),
            schemas.Create(
                goal="先看看能用哪个图片模型，告诉我费用",
                request_key=uuid.uuid4(),
                model_provider=text["provider_name"],
            ),
        )
        rid = run["id"]
        db.commit()
    for _ in range(4):
        with sessions() as db:
            ticket = service.claim(db, rid)
            db.commit()
        assert ticket
        runtime.execute(ticket, sessions)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        assert run.status == "succeeded", service.out(db, run)
        assert len(calls) == 4 and run.policy["budget_points"] == 0
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        names = {
            item["name"]
            for item in capabilities.catalogue(
                group="media", ctx=context(db, ids), context=ToolContext()
            )
        }
        assert "media.models" in names
