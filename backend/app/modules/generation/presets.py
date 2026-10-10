"""Versioned, server-owned defaults and atomic provider onboarding.

Only protocols with implemented adapters are advertised. Saving does not claim a
successful paid generation. Preset IDs are stable even if display names change.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select

from app.core.crypto import decrypt
from app.core.errors import CapabilityUnsupported, Conflict
from app.models.generation import CreativeModel, ModelChannel, ProviderConfig
from app.models.identity import Project
from app.modules.generation import channels, model_catalog

PRESETS = {
    "ark": {
        "name": "火山方舟",
        "description": "Seedream 图片与 Seedance 视频，一份密钥统一连接。",
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3",
        "key_url": "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
        "note": (
            "使用火山方舟 API Key，并在方舟开通所选模型。即梦网站账号和火山 AK/SK 不能替代此密钥。"
        ),
        "models": {
            "seedream-4.5": {
                "name": "Seedream 4.5",
                "model": "doubao-seedream-4-5-251128",
                "protocol": "ark_image",
                "modality": "image",
                "max_reference_images": 14,
                "parameters": {"size": ["2K", "4K", "2048x2048", "2560x1440", "1440x2560"]},
            },
            "seedance-1.5-pro": {
                "name": "Seedance 1.5 Pro",
                "model": "doubao-seedance-1-5-pro-251215",
                "protocol": "ark_video",
                "modality": "video",
                "max_reference_images": 2,
                "max_video_seconds": 12,
                "parameters": {
                    "duration": [5, 4, 6, 8, 10, 12],
                    "resolution": ["720p", "480p"],
                    "aspect_ratio": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
                },
            },
        },
    },
    "minimax": {
        "name": "MiniMax 海螺",
        "description": "文字或一张参考图，生成海螺视频。",
        "endpoint": "https://api.minimax.cn/v1",
        "key_url": "https://platform.minimax.cn/user-center/basic-information/interface-key",
        "note": "使用 MiniMax 开放平台 API Key，并确认账户已开通视频生成。1080P 支持 6 秒。",
        "models": {
            "hailuo-2.3": {
                "name": "Hailuo 2.3",
                "model": "MiniMax-Hailuo-2.3",
                "protocol": "minimax_video",
                "modality": "video",
                "max_reference_images": 1,
                "max_video_seconds": 10,
                "parameters": {"duration": [6, 10], "resolution": ["768P", "1080P"]},
            },
        },
    },
}


# Official IDs and conservative controls verified against Ark's model list and
# image/video API references on 2026-09-28. Availability is account dependent.
PRESETS["ark"]["models"].update(
    {
        "seedream-5.0-lite": {
            "name": "Seedream 5.0 Lite",
            "model": "doubao-seedream-5-0-260128",
            "protocol": "ark_image",
            "modality": "image",
            "max_reference_images": 14,
            "parameters": {"size": ["2K", "3K", "4K"]},
        },
        "seedream-4.0": {
            "name": "Seedream 4.0",
            "model": "doubao-seedream-4-0-250828",
            "protocol": "ark_image",
            "modality": "image",
            "max_reference_images": 14,
            "parameters": {"size": ["2K", "4K"]},
        },
        "seedance-2.0": {
            "name": "Seedance 2.0",
            "model": "doubao-seedance-2-0-260128",
            "protocol": "ark_video",
            "modality": "video",
            "max_reference_images": 2,
            "max_video_seconds": 15,
            "parameters": {
                "duration": [5, 4, 6, 8, 10, 12, 15],
                "resolution": ["720p", "480p"],
                "aspect_ratio": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
            },
        },
    }
)


# Lifecycle data checked with GetFoundationModelVersion on 2026-09-28.
# Activation state alone does not mean a concrete model version is still serving.
PRESETS["ark"]["models"]["seedream-4.5"].update(
    recommended=False, lifecycle_note="已进入退役阶段，建议选择较新图片模型"
)
PRESETS["ark"]["models"]["seedance-1.5-pro"].update(
    recommended=False, lifecycle_note="已到官方下线日期（2026-09-21），请选择 Seedance 2.0"
)
PRESETS["ark"]["models"]["seedream-4.0"]["recommended"] = False
PRESETS["ark"]["models"] = dict(
    sorted(PRESETS["ark"]["models"].items(), key=lambda item: not item[1].get("recommended", True))
)


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    preset_id: str = Field(min_length=1, max_length=60)
    token: SecretStr = Field(min_length=1, max_length=8192)
    models: list[str] = Field(min_length=1, max_length=20)
    request_id: uuid.UUID
    name: str | None = Field(default=None, min_length=1, max_length=100)
    endpoint: str | None = Field(default=None, max_length=512)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value):
        return channels.ChannelIn.endpoint_url(value) if value else value


def catalog():
    return [
        {"id": key, **p, "models": [{"id": k, **v} for k, v in p["models"].items()]}
        for key, p in PRESETS.items()
    ]


def connect(db, ctx, data):
    preset = PRESETS.get(data.preset_id)
    if (
        not preset
        or len(set(data.models)) != len(data.models)
        or any(k not in preset["models"] for k in data.models)
    ):
        raise CapabilityUnsupported("请选择服务列表中支持的模型")
    token = data.token.get_secret_value().strip()
    if not token:
        raise CapabilityUnsupported("请填写服务密钥")
    channel_data = channels.ChannelIn(
        name=data.name or preset["name"],
        token=token,
        endpoint=data.endpoint or preset["endpoint"],
        request_id=data.request_id,
    )
    # One project lock and one request transaction cover channel, profiles and routes.
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    ch = db.get(ModelChannel, data.request_id)
    if ch and (
        ch.project_id != ctx.project.id
        or ch.endpoint != channel_data.endpoint
        or decrypt(ch.credentials_encrypted) != token
    ):
        raise Conflict("此保存请求已使用，请关闭窗口后重新连接服务")
    if not ch:
        # Reopening the wizard with the same service key adds models, not duplicate channels.
        ch = next(
            (
                r
                for r in db.scalars(
                    select(ModelChannel).where(
                        ModelChannel.project_id == ctx.project.id,
                        ModelChannel.endpoint == channel_data.endpoint,
                        ModelChannel.enabled.is_(True),
                    )
                )
                if decrypt(r.credentials_encrypted) == token
            ),
            None,
        )
    ch = ch or channels.save_channel(db, ctx, channel_data)
    if not ch.enabled:
        raise Conflict("此服务已停用，请先在供应商渠道中启用")
    result = []
    for key in data.models:
        spec = preset["models"][key]
        profiles = list(
            db.scalars(select(ProviderConfig).where(ProviderConfig.project_id == ctx.project.id))
        )
        profile = next(
            (
                p
                for p in profiles
                if p.config.get("channel_id") == str(ch.id)
                and p.config.get("protocol") == spec["protocol"]
                and p.config.get("model") == spec["model"]
            ),
            None,
        )
        if profile and not profile.enabled:
            raise Conflict(f"{spec['name']} 已停用，请先在渠道中启用")
        if not profile:
            profile = channels.save_model(
                db,
                ctx,
                ch.id,
                channels.ModelIn(
                    **{k: v for k, v in spec.items() if k not in ("recommended", "lifecycle_note")},
                    request_id=uuid.uuid5(ch.id, data.preset_id + ":" + key),
                ),
            )
        models = model_catalog.rows(db, ctx.project.id)
        bound = next(
            (
                m
                for m in models
                if any(r["profile_id"] == str(profile.id) for r in m.definition.get("routes", []))
            ),
            None,
        )
        if bound:
            if not bound.enabled:
                raise Conflict(f"{bound.name} 已停用，请先在创作模型中启用")
            result.append(model_catalog.public(db, bound))
            continue
        ident = uuid.uuid5(ctx.project.id, "preset:" + data.preset_id + ":" + key)
        existing = db.get(CreativeModel, ident)
        if existing:
            if not existing.enabled:
                raise Conflict(f"{existing.name} 已停用，请先在创作模型中启用")
            if len(existing.definition.get("routes", [])) >= 30:
                raise CapabilityUnsupported("此模型已有 30 条线路，请先整理现有线路")
            definition = {
                **existing.definition,
                "routes": [
                    *existing.definition.get("routes", []),
                    {"profile_id": str(profile.id), "priority": 100, "weight": 100},
                ],
            }
            value = model_catalog.CreativeModelIn(
                **definition,
                name=existing.name,
                modality=existing.modality,
                revision=existing.revision,
            )
            row = model_catalog.save(db, ctx, value, existing.id)
        else:
            row = model_catalog.save(
                db,
                ctx,
                model_catalog.CreativeModelIn(
                    name=spec["name"],
                    modality=spec["modality"],
                    request_id=ident,
                    is_default=not any(
                        m.modality == spec["modality"] and m.definition.get("is_default")
                        for m in models
                    ),
                    routes=[model_catalog.RouteIn(profile_id=profile.id)],
                ),
            )
        result.append(model_catalog.public(db, row))
    return {
        "channel_id": ch.id,
        "models": result,
        "message": "配置已保存，模型已加入创作列表；尚未进行实际生成验证。",
    }
