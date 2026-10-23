"""Declared media inputs and fixed text-model routes, without provider requests."""

# ruff: noqa: F811
import base64
import io
import uuid
import wave

import pytest
from PIL import Image
from pydantic import ValidationError

from app.core.crypto import encrypt
from app.core.errors import CapabilityUnsupported, Conflict
from app.models.generation import CreativeModel
from app.models.personal_model import PersonalModelService
from app.modules.generation import channels, model_catalog
from app.modules.harness import kernel
from app.modules.harness.kernel_modalities import (
    InputCapabilities,
    input_contract,
    resolve_cloud_input,
    validate_media_parts,
)
from tests.test_harness_context import context
from tests.test_media_workflow import studio  # noqa: F401


def vision():
    return InputCapabilities(modalities=["text", "image"], max_images=2).model_dump()


def image_part():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format="PNG")
    return {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        },
    }


def audio_part(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * int(16000 * seconds))
    return {
        "type": "input_audio",
        "input_audio": {"format": "wav", "data": base64.b64encode(output.getvalue()).decode()},
    }


def test_model_names_and_generation_features_never_imply_vision():
    value = input_contract(
        "cloud_llm",
        {"model": "vision-image-audio-super-model", "max_reference_images": 14, "modality": "text"},
    )
    assert value["modalities"] == ["text"]
    with pytest.raises(CapabilityUnsupported, match="未声明图片"):
        validate_media_parts(value, [image_part()])
    assert (
        validate_media_parts(value, [{"type": "text", "text": "已有转写：你好"}])["audio_clips"]
        == 0
    )
    with pytest.raises(CapabilityUnsupported, match="未声明音频"):
        validate_media_parts(value, [audio_part()])


def test_explicit_adapter_contract_and_media_quantity_are_enforced(monkeypatch):
    def adapter(*_):
        return {}

    adapter.input_capabilities = vision()
    monkeypatch.setitem(kernel._KERNELS, "declared_vision", adapter)
    limits = input_contract("declared_vision")
    assert validate_media_parts(limits, [image_part(), image_part()])["images"] == 2
    with pytest.raises(CapabilityUnsupported, match="数量"):
        validate_media_parts(limits, [image_part()] * 3)
    with pytest.raises(CapabilityUnsupported, match="网址"):
        validate_media_parts(
            limits, [{"type": "image_url", "image_url": {"url": "https://private.invalid/picture"}}]
        )
    with pytest.raises(CapabilityUnsupported, match="格式"):
        validate_media_parts(limits, [{"type": "video_url", "video_url": "file:///private"}])


def test_audio_duration_and_body_bounds_use_real_decoding():
    limits = InputCapabilities(modalities=["text", "audio"], max_audio_clips=1, max_audio_seconds=1)
    assert validate_media_parts(limits, [audio_part()])["audio_clips"] == 1
    with pytest.raises(CapabilityUnsupported, match="时长"):
        validate_media_parts(limits, [audio_part(1.1)])
    with pytest.raises(CapabilityUnsupported, match="过大"):
        validate_media_parts(limits.model_copy(update={"max_media_bytes": 1024}), [audio_part()])
    with pytest.raises(CapabilityUnsupported, match="编码无效"):
        validate_media_parts(
            vision(), [{"type": "image_url", "image_url": {"url": "data:image/png;base64,%%%"}}]
        )


@pytest.mark.parametrize(
    "value",
    [
        {"modalities": ["image"], "max_images": 1},
        {"modalities": ["text", "image"]},
        {"modalities": ["text"], "max_images": 1},
        {"modalities": ["text", "audio"], "max_audio_clips": 1},
        {"modalities": ["text", "audio"], "max_audio_clips": 1, "max_audio_seconds": float("inf")},
    ],
)
def test_inconsistent_or_nonfinite_input_contract_is_rejected(value):
    with pytest.raises(ValidationError):
        InputCapabilities(**value)


def test_project_model_declared_inputs_and_concrete_route_are_persisted(studio, monkeypatch):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        ch = channels.save_channel(
            db,
            ctx,
            channels.ChannelIn(
                name="test", endpoint="https://fixture.invalid/v1", token="isolated-placeholder"
            ),
        )
        first = channels.save_model(
            db,
            ctx,
            ch.id,
            channels.ModelIn(
                name="vision",
                model="model-a",
                protocol="cloud_llm",
                modality="text",
                input_capabilities=vision(),
            ),
        )
        second = channels.save_model(
            db,
            ctx,
            ch.id,
            channels.ModelIn(name="text", model="model-b", protocol="cloud_llm", modality="text"),
        )
        assert channels.public_model(first)["input_capabilities"] == vision()
        logical = CreativeModel(
            project_id=ctx.project.id,
            name="weighted",
            modality="text",
            definition={
                "routes": [
                    {"profile_id": str(row.id), "enabled": True, "priority": 1, "weight": 1}
                    for row in (first, second)
                ]
            },
        )
        db.add(logical)
        db.commit()
        monkeypatch.setattr(
            model_catalog,
            "ordered",
            lambda candidates: sorted(candidates, key=lambda c: c["profile_id"] != str(first.id)),
        )
        selector = model_catalog.PREFIX + str(logical.id)
        resolved = resolve_cloud_input(db, ctx, selector)
        assert resolved["config"]["model"] == "model-a"
        assert resolved["input_contract"]["modalities"] == ["text", "image"]
        monkeypatch.setattr(
            model_catalog, "ordered", lambda *_: pytest.fail("固定线路不能再次随机选择")
        )
        fixed = resolve_cloud_input(db, ctx, selector, preferred_profile=resolved["profile"])
        assert fixed == resolved
        # An old client renaming a model must preserve its declared input abilities.
        channels.save_model(
            db,
            ctx,
            ch.id,
            channels.ModelIn(
                name="renamed",
                model="model-a",
                protocol="cloud_llm",
                modality="text",
                revision=first.config["revision"],
            ),
            first.id,
        )
        assert first.config["input_capabilities"] == vision()
        first.enabled = False
        db.commit()
        with pytest.raises(Conflict, match="线路"):
            resolve_cloud_input(db, ctx, selector, preferred_profile=resolved["profile"])
        changed = channels.save_model(
            db,
            ctx,
            ch.id,
            channels.ModelIn(
                name="different model",
                model="model-c",
                protocol="cloud_llm",
                modality="text",
                revision=first.config["revision"],
            ),
            first.id,
        )
        assert channels.public_model(changed)["input_capabilities"]["modalities"] == ["text"]


def test_personal_input_capabilities_api_and_owner_guard(studio):
    client, sessions, ids = studio
    with sessions() as db:
        row = PersonalModelService(
            user_id=ids["user"],
            name="private",
            preset_id="custom-fixture",
            endpoint="https://fixture.invalid/v1",
            credentials_encrypted=encrypt("placeholder-only"),
            models={
                "model-a": {
                    "name": "A",
                    "model": "model-a",
                    "modality": "text",
                    "protocol": "cloud_llm",
                }
            },
        )
        db.add(row)
        db.commit()
        ident, revision = row.id, row.revision
    path = f"/api/v1/me/model-services/{ident}/models/model-a/input-capabilities"
    result = client.patch(path, json={"revision": revision, "input_capabilities": vision()})
    assert result.status_code == 200, result.text
    assert result.json()["models"][0]["input_capabilities"] == vision()
    assert (
        client.patch(path, json={"revision": revision, "input_capabilities": vision()}).status_code
        == 409
    )
    with sessions() as db:
        selected = resolve_cloud_input(db, context(db, ids), f"personal_{ident.hex}_model-a")
        assert selected["input_contract"]["max_images"] == 2
        assert selected["profile"]["kind"] == "personal"
    assert (
        client.patch(
            f"/api/v1/me/model-services/{uuid.uuid4()}/models/model-a/input-capabilities",
            json={"revision": 1, "input_capabilities": vision()},
        ).status_code
        == 404
    )
