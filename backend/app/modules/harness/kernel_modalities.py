"""Declared model inputs, fixed model-route resolution and bounded local media parts.

Output modality and generation reference limits are not evidence of vision/audio
understanding. No model-name heuristics and no remote URL loading are used here.
"""

import base64
import binascii
import io
import wave
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.errors import CapabilityUnsupported, Conflict


class InputCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    modalities: list[Literal["text", "image", "audio"]] = Field(
        default_factory=lambda: ["text"], max_length=3
    )
    max_images: int = Field(default=0, ge=0, le=16)
    max_audio_clips: int = Field(default=0, ge=0, le=4)
    max_audio_seconds: float = Field(default=0, ge=0, le=120)
    max_media_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)

    @model_validator(mode="after")
    def matching_limits(self):
        if "text" not in self.modalities or len(set(self.modalities)) != len(self.modalities):
            raise ValueError("输入能力必须包含文字，且不能重复")
        if ("image" in self.modalities) != (self.max_images > 0):
            raise ValueError("图片理解能力与最多图片数必须一致")
        if ("audio" in self.modalities) != (
            self.max_audio_clips > 0 and self.max_audio_seconds > 0
        ):
            raise ValueError("音频理解能力必须同时声明片段数与时长")
        if "audio" not in self.modalities and (self.max_audio_clips or self.max_audio_seconds):
            raise ValueError("未启用音频理解时不能配置音频上限")
        return self


def input_contract(engine: str, model_config: dict | None = None) -> dict:
    """Use the *same resolved profile* as the credentials used for this decision."""
    from app.modules.harness.kernel import get_kernel

    adapter = get_kernel(engine)
    declaration = (
        (model_config or {}).get("input_capabilities")
        if engine == "cloud_llm"
        else getattr(adapter, "input_capabilities", None)
    )
    try:
        return InputCapabilities.model_validate(declaration or {}).model_dump(mode="json")
    except ValidationError:
        raise CapabilityUnsupported("模型输入能力声明无效，请在模型服务中重新配置") from None


def resolve_cloud_input(db, ctx, provider=None, *, preferred_profile=None) -> dict:
    """Resolve once and retain concrete route identity; returned config is private.

    Persist only ``profile`` and a digest of config+input_contract. Never return
    config to an API response, event, context packet or audit log.
    """
    from app.core.config import settings
    from app.core.crypto import decrypt
    from app.modules.generation import channels, model_catalog
    from app.modules.harness.context import revalidate_context

    live = revalidate_context(db, ctx)
    if preferred_profile and preferred_profile.get("selector") != provider:
        raise Conflict("文字模型选择已变化，请暂停后确认切换")
    pc = None
    if preferred_profile and preferred_profile.get("kind") == "environment":
        if provider is not None:
            raise Conflict("固定文字模型配置不匹配")
    else:
        name = preferred_profile.get("provider_name") if preferred_profile else provider
        if preferred_profile and not name:
            raise Conflict("固定文字模型配置已失效，请重新确认模型")
        if preferred_profile and (provider or "").startswith(model_catalog.PREFIX):
            logical = model_catalog.get_model(db, live.project.id, provider)
            candidates, _ = model_catalog.all_candidates(db, logical)
            if (
                not logical.enabled
                or logical.modality != "text"
                or not any(
                    item["profile_id"] == preferred_profile.get("profile_id") for item in candidates
                )
            ):
                raise Conflict("原文字模型线路已停用或移除，请暂停后确认切换")
        try:
            pc = (
                channels.selected_llm(db, live.project.id, name, user_id=live.user.id)
                if name
                else channels.llm_config(db, live.project.id)
            )
        except Exception as exc:
            from app.core.errors import AppError

            if preferred_profile and isinstance(exc, AppError):
                raise Conflict("固定文字模型已不可用，请暂停后确认切换") from None
            raise
        if (
            preferred_profile
            and pc
            and preferred_profile.get("profile_id")
            and str(getattr(pc, "id", "")) != preferred_profile["profile_id"]
        ):
            raise Conflict("固定文字模型身份已变化，请暂停后确认切换")
    if pc is None:
        profile = {"kind": "environment", "selector": provider}
        config = {
            "base_url": settings.llm_base_url,
            "api_key": settings.llm_api_key,
            "model": settings.llm_model,
        }
        declaration = {}
    else:
        profile = {
            "kind": "personal" if (provider or "").startswith("personal_") else "project",
            "selector": provider,
            "provider_name": getattr(pc, "provider_name", None) or provider,
            "profile_id": str(pc.id) if getattr(pc, "id", None) else None,
        }
        config = {
            "base_url": pc.endpoint,
            "api_key": decrypt(pc.credentials_encrypted),
            "model": (pc.config or {}).get("model"),
        }
        declaration = pc.config or {}
    if not all(isinstance(v, str) and v.strip() for v in config.values()):
        raise CapabilityUnsupported("文字模型尚未完整配置，请检查模型服务")
    return {
        "config": config,
        "input_contract": input_contract("cloud_llm", declaration),
        "profile": profile,
    }


def validate_media_parts(contract: InputCapabilities | dict, parts: list[dict]) -> dict:
    """Validate ephemeral local parts before any provider request; never omit silently."""
    limits = InputCapabilities.model_validate(contract)
    if not isinstance(parts, list) or len(parts) > 64:
        raise CapabilityUnsupported("媒体上下文数量超出上限")
    images = audio = size = 0
    for part in parts:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            if (
                set(part) != {"type", "text"}
                or not isinstance(part["text"], str)
                or len(part["text"]) > 32000
            ):
                raise CapabilityUnsupported("媒体说明格式无效或过长")
            continue
        if kind == "image_url":
            if "image" not in limits.modalities:
                raise CapabilityUnsupported(
                    "当前文字模型未声明图片理解能力，请在模型服务中启用或切换支持的模型"
                )
            value = part.get("image_url", {})
            url = value.get("url") if isinstance(value, dict) else None
            if not isinstance(url, str) or not any(
                url.startswith(f"data:image/{mime};base64,") for mime in ("jpeg", "png", "webp")
            ):
                raise CapabilityUnsupported("图片上下文必须来自已读取的本地素材，不能使用任意网址")
            data = url.split(",", 1)[1]
            images += 1
        elif kind == "input_audio":
            if "audio" not in limits.modalities:
                raise CapabilityUnsupported(
                    "当前文字模型未声明音频理解能力；已有转写只能作为文字证据"
                )
            value = part.get("input_audio", {})
            if not isinstance(value, dict) or value.get("format") != "wav":
                raise CapabilityUnsupported("当前媒体上下文只支持已解码的 WAV 音频")
            data = value.get("data")
            audio += 1
        else:
            raise CapabilityUnsupported(
                "此媒体输入格式尚不支持；视频须先读取选定范围的关键帧或音轨"
            )
        if not isinstance(data, str) or len(data) > limits.max_media_bytes * 4 / 3 + 4:
            raise CapabilityUnsupported("媒体上下文过大，请缩小选区或读取范围")
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise CapabilityUnsupported("媒体上下文编码无效") from None
        if not raw:
            raise CapabilityUnsupported("媒体上下文为空")
        size += len(raw)
        if kind == "input_audio":
            try:
                with wave.open(io.BytesIO(raw), "rb") as source:
                    seconds = source.getnframes() / source.getframerate()
            except (wave.Error, EOFError, ZeroDivisionError):
                raise CapabilityUnsupported("音频上下文不是有效的 WAV 文件") from None
            if seconds > limits.max_audio_seconds:
                raise CapabilityUnsupported("音频片段超过当前模型声明的时长上限")
    if (
        images > limits.max_images
        or audio > limits.max_audio_clips
        or size > limits.max_media_bytes
    ):
        raise CapabilityUnsupported("媒体上下文超过当前模型声明的数量或大小上限")
    return {"images": images, "audio_clips": audio, "media_bytes": size}
