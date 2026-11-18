"""Project-scoped channels and model profiles, reusing stable provider/job identities."""

import copy
import uuid
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.adapters.generation.litellm_media import SDK_PROVIDERS
from app.adapters.generation.provider_media import MEDIA_ADAPTERS
from app.core import audit
from app.core.crypto import encrypt
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, ProviderError
from app.models.generation import CreativeModel, GenerationJob, ModelChannel, ProviderConfig
from app.modules.generation import minimax_models
from app.modules.generation.model_catalog import Price
from app.modules.generation.model_scope import lock_model_scope
from app.modules.harness.kernel_modalities import InputCapabilities

MEDIA_BRANDS = {
    "xai": "xAI",
    "minimax": "MiniMax",
    "zhipu": "智谱",
    "wavespeed": "WaveSpeed",
    "kling": "可灵",
    "heygen": "HeyGen",
    "vidu": "Vidu",
    "bfl": "Black Forest Labs",
    "bailian": "阿里云百炼",
    "openrouter": "OpenRouter",
}

PROTOCOLS = {
    **{
        adapter.name: {
            "name": MEDIA_BRANDS[adapter.name.rsplit("_", 1)[0]]
            + (" 图片" if adapter.modality == "image" else " 视频"),
            "types": [adapter.modality],
            "parameter_keys": sorted(adapter.allowed_parameters),
            "max_refs": adapter.max_refs,
        }
        for adapter in MEDIA_ADAPTERS
    },
    "litellm": {
        "name": "LiteLLM 多供应商 SDK",
        "types": ["image", "video"],
        "sdk_providers": SDK_PROVIDERS,
    },
    "gpt_image": {"name": "OpenAI Images 兼容", "types": ["image"]},
    "openai_video": {"name": "OpenAI Videos 兼容", "types": ["video"]},
    "video_v1": {"name": "Video v1 JSON（文字 / 参考图）", "types": ["video"]},
    "ark_image": {"name": "火山方舟图片", "types": ["image"]},
    "ark_video": {"name": "火山方舟视频", "types": ["video"]},
    "minimax_video": {"name": "MiniMax 海螺视频", "types": ["video"]},
    minimax_models.H3_PROTOCOL: {
        "name": "MiniMax H3 视频",
        "types": ["video"],
        "parameter_keys": ["duration", "resolution", "aspect_ratio"],
        "max_refs": 2,
    },
    "cloud_llm": {
        "name": "文字模型 · LiteLLM（含 OpenAI 兼容）",
        "types": ["text"],
        "sdk_providers": SDK_PROVIDERS,
    },
    "jimeng": {"name": "旧版自定义 /submit · /status", "types": ["image", "video"]},
    "mock": {"name": "离线测试", "types": ["image", "video"]},
}


class ChannelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(default="", max_length=512)
    token: str | None = Field(default=None, max_length=8192)
    provider_template_id: str | None = Field(
        default=None, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )
    clear_token: bool = False
    enabled: bool = True
    revision: int | None = None
    request_id: uuid.UUID | None = None

    @field_validator("endpoint")
    @classmethod
    def endpoint_url(cls, value):
        if not value:
            return value
        u = urlsplit(value)
        if (
            u.scheme not in ("http", "https")
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
        ):
            raise ValueError("服务地址须为 HTTP(S) 基础地址，不能包含密钥、查询参数或账号")
        return value.rstrip("/")

    @model_validator(mode="after")
    def credential_action(self):
        if self.token and self.clear_token:
            raise ValueError("更换和清除密钥不能同时执行")
        return self


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=128)
    protocol: str = Field(min_length=1, max_length=64)
    sdk_provider: str = Field(default="openai", max_length=40)
    modality: Literal["image", "video", "text"]
    enabled: bool = True
    is_default: bool = False
    revision: int | None = None
    request_id: uuid.UUID | None = None
    input_capabilities: InputCapabilities | None = None
    max_reference_images: int = Field(default=0, ge=0, le=16)
    max_reference_videos: int = Field(default=0, ge=0, le=10)
    mask_editing: bool = False
    max_video_seconds: float = Field(default=10, ge=1, le=120, allow_inf_nan=False)
    parameters: dict[str, list[str | int]] = Field(default_factory=dict)
    price: Price = Field(default_factory=Price)

    @model_validator(mode="after")
    def supported(self):
        if self.protocol not in PROTOCOLS:
            raise ValueError("未接入此协议")
        if self.protocol in ("litellm", "cloud_llm"):
            if self.sdk_provider not in SDK_PROVIDERS[self.modality]:
                raise ValueError("所选供应商不支持该 SDK 接入用途")
        elif self.sdk_provider != "openai":
            raise ValueError("只有 SDK 接入方式可以设置 SDK 供应商")
        if self.protocol == "litellm":
            if self.modality == "video" and self.max_reference_images > 1:
                raise ValueError("当前 SDK 视频接入最多支持一张起始参考图")
            if self.mask_editing and self.sdk_provider != "openai":
                raise ValueError("当前仅开放 OpenAI SDK 图片蒙版编辑")

        if self.input_capabilities is not None and self.modality != "text":
            raise ValueError("输入理解能力仅用于文字模型")
        if self.max_reference_videos and (self.protocol != "video_v1" or self.modality != "video"):
            raise ValueError("参考视频仅用于支持此能力的 Video v1 视频模型")
        if self.price.mode == "per_second" and self.modality != "video":
            raise ValueError("按秒计价仅用于视频")
        if self.modality == "text" and self.price.mode != "unconfigured":
            raise ValueError("文字计价暂未接入")
        if self.is_default and self.modality != "text":
            raise ValueError("目前默认模型用于文字创作；图片和视频在创作时选择")
        if self.modality not in PROTOCOLS[self.protocol]["types"]:
            raise ValueError("协议不支持所选模型类型")
        if self.mask_editing and (
            self.protocol not in ("gpt_image", "litellm")
            or self.modality != "image"
            or not self.max_reference_images
        ):
            raise ValueError("蒙版编辑需要支持参考图的 Images 协议")
        if self.protocol == "openai_video" and self.max_reference_images > 1:
            raise ValueError("Videos 协议最多接收一张起始参考图")
        if self.protocol == "video_v1" and self.max_reference_images > 14:
            raise ValueError("Video v1 参考图上限不能超过 14；请按实际渠道能力填写")
        if self.protocol == "video_v1" and not self.parameters.get("duration"):
            raise ValueError("Video v1 必须配置时长选项，避免使用上游默认时长")
        limits = {"ark_image": 14, "ark_video": 2, "minimax_video": 1}
        limit = PROTOCOLS[self.protocol].get("max_refs", limits.get(self.protocol, 16))
        if self.max_reference_images > limit:
            raise ValueError("参考图上限超出该接口支持的范围")
        if self.modality == "text" and (self.parameters or self.max_reference_images):
            raise ValueError("文字模型不使用生成参数与参考图上限")
        allowed = (
            {"size", "quality"}
            if self.modality == "image"
            else {"duration", "size", "aspect_ratio", "resolution"}
        )
        if self.protocol == "openai_video" or (
            self.protocol == "litellm" and self.modality == "video"
        ):
            allowed = {"duration", "size"}
        if self.protocol == "ark_image":
            allowed = {"size"}
        if self.protocol in ("ark_video", "video_v1"):
            allowed = {"duration", "resolution", "aspect_ratio"}
        if self.protocol == "minimax_video":
            allowed = {"duration", "resolution"}
        allowed = set(PROTOCOLS[self.protocol].get("parameter_keys", allowed))
        if set(self.parameters) - allowed:
            raise ValueError("包含当前协议未支持的参数")
        for key, values in self.parameters.items():
            if not values or len(values) > 30 or any(len(str(v)) > 80 for v in values):
                raise ValueError("每个参数须提供1至30个简短可选值")
            if key == "duration" and any(
                not str(v).isdigit() or not 0 < int(v) <= self.max_video_seconds for v in values
            ):
                raise ValueError("时长选项须为正整数，且不能超过最大时长")
            if key == "duration":
                self.parameters[key] = [int(v) for v in values]
        return self


def channel(db, project_id, channel_id, lock=False):
    row = db.scalar(
        select(ModelChannel)
        .where(ModelChannel.id == channel_id, ModelChannel.project_id == project_id)
        .with_for_update()
        if lock
        else select(ModelChannel).where(
            ModelChannel.id == channel_id, ModelChannel.project_id == project_id
        )
    )
    if not row:
        raise NotFound("渠道不存在")
    return row


def profile(db, project_id, model_id):
    row = db.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.id == model_id, ProviderConfig.project_id == project_id)
        .with_for_update()
    )
    if not row or not row.config.get("channel_id"):
        raise NotFound("模型不存在")
    return row


def profile_modality(row):
    return row.config.get("modality") or ("text" if row.kind == "llm" else "image")


def public_model(row, endpoint=None):
    c = minimax_models.video_profile(row.config or {}, endpoint)
    return {
        "id": row.id,
        "provider_name": row.provider_name,
        "name": c.get("display_name", row.provider_name),
        "model": c.get("model", ""),
        "protocol": c.get("protocol", row.provider_name),
        "sdk_provider": c.get("sdk_provider", "openai"),
        "modality": profile_modality(row),
        "enabled": row.enabled,
        "is_default": bool(c.get("is_default")),
        "revision": c.get("revision", 1),
        "input_capabilities": c.get("input_capabilities", InputCapabilities().model_dump()),
        "max_reference_images": c.get("max_reference_images", 0),
        "max_reference_videos": c.get("max_reference_videos", 0),
        "mask_editing": c.get("mask_editing", False),
        "max_video_seconds": c.get("max_video_seconds", 10),
        "parameters": {k: v.get("enum", []) for k, v in c.get("param_schema", {}).items()},
        "price": c.get("price", {"mode": "unconfigured", "amount": "0"}),
    }


def catalog(db, project_id):
    models = list(
        db.scalars(
            select(ProviderConfig)
            .where(ProviderConfig.project_id == project_id)
            .order_by(ProviderConfig.created_at)
        )
    )
    rows = list(
        db.scalars(
            select(ModelChannel)
            .where(ModelChannel.project_id == project_id)
            .order_by(ModelChannel.created_at)
        )
    )
    return {
        "channels": [
            {
                "id": r.id,
                "name": r.name,
                "endpoint": r.endpoint,
                "provider_template_id": r.provider_template_id,
                "known_outputs": minimax_models.MODEL_OUTPUTS
                if r.provider_template_id == "minimax"
                else {},
                "enabled": r.enabled,
                "has_token": bool(r.credentials_encrypted),
                "revision": r.revision,
                "models": [
                    public_model(p, r.endpoint)
                    for p in models
                    if p.config.get("channel_id") == str(r.id)
                ],
            }
            for r in rows
        ],
        "legacy_count": sum(
            not p.config.get("channel_id") and p.provider_name in PROTOCOLS for p in models
        ),
        "protocols": PROTOCOLS,
    }


def require_matching_credential_origin(ch, data):
    if ch and ch.credentials_encrypted and not data.token and not data.clear_token:
        old, new = urlsplit(ch.endpoint), urlsplit(data.endpoint)
        if (old.scheme, old.netloc.lower()) != (new.scheme, new.netloc.lower()):
            raise CapabilityUnsupported("切换服务地址的域名或协议后，请重新填写密钥")


def save_channel(db, ctx, data, channel_id=None):
    # Serialize creates and defaults within a project; request identities make retries safe.
    lock_model_scope(db, ctx.project.id)
    if channel_id:
        row = channel(db, ctx.project.id, channel_id, True)
        if data.revision != row.revision:
            raise Conflict("渠道已被修改，请关闭编辑后重新打开，核对再保存")
        row.revision += 1
    else:
        if data.request_id:
            existing = db.get(ModelChannel, data.request_id)
            if existing:
                if existing.project_id != ctx.project.id:
                    raise Conflict("创建标识已使用，请重新创建")
                return existing
        row = ModelChannel(id=data.request_id or uuid.uuid4(), project_id=ctx.project.id)
        db.add(row)
    require_matching_credential_origin(row, data)
    row.name, row.endpoint, row.enabled = data.name, data.endpoint, data.enabled
    if "provider_template_id" in data.model_fields_set:
        row.provider_template_id = data.provider_template_id
    if data.clear_token:
        row.credentials_encrypted = None
    elif data.token:
        row.credentials_encrypted = encrypt(data.token)
    db.flush()
    audit.record(
        db,
        action="model_channel.save",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="model_channel",
        target_id=row.id,
        detail={
            "enabled": row.enabled,
            "revision": row.revision,
            "credential_changed": bool(data.token or data.clear_token),
        },
    )
    return row


def validate_model_output(data, template_id):
    expected = minimax_models.expected_output(
        data.model, template_id=template_id, protocol=data.protocol
    )
    if expected and data.enabled and data.modality != expected:
        purpose = {"text": "文字与理解", "image": "图片生成", "video": "视频生成"}[expected]
        raise CapabilityUnsupported(f"{data.model} 属于「{purpose}」，请更正模型用途")


def save_model(db, ctx, channel_id, data, model_id=None):
    lock_model_scope(db, ctx.project.id)
    ch = channel(db, ctx.project.id, channel_id, True)
    if data.protocol != "mock" and not ch.endpoint:
        raise CapabilityUnsupported("请先为渠道填写服务地址")
    if model_id:
        row = profile(db, ctx.project.id, model_id)
        if row.config["channel_id"] != str(ch.id):
            raise NotFound("模型不属于该渠道")
        if data.revision != row.config.get("revision", 1):
            raise Conflict("模型已被修改，请关闭编辑后重新打开，核对再保存")
    else:
        if data.request_id:
            old = db.get(ProviderConfig, data.request_id)
            if old:
                if old.project_id != ctx.project.id or old.config.get("channel_id") != str(ch.id):
                    raise Conflict("创建标识已使用，请重新创建")
                return old
        row = ProviderConfig(
            id=data.request_id or uuid.uuid4(),
            project_id=ctx.project.id,
            provider_name=("platform_" if ctx.project.id is None else "model_") + uuid.uuid4().hex,
            config={},
        )
    validate_model_output(data, ch.provider_template_id)
    # Channel and scope locks also cover batch writes and concurrent administrators.
    # Existing duplicates retain their IDs so linked defaults and jobs can still be edited.
    if ((row.config.get("model") or "").strip(), profile_modality(row)) != (
        data.model,
        data.modality,
    ):
        matches = db.scalars(
            select(ProviderConfig).where(
                ProviderConfig.project_id == ctx.project.id,
                ProviderConfig.config["channel_id"].astext == str(ch.id),
                func.btrim(ProviderConfig.config["model"].astext) == data.model,
                ProviderConfig.id != row.id,
            )
        )
        if any(profile_modality(other) == data.modality for other in matches):
            raise Conflict("这个模型已配置到当前用途，请编辑已有配置")
    if not model_id:
        db.add(row)
    if data.is_default:
        if not data.enabled or not ch.enabled:
            raise CapabilityUnsupported("默认模型及其渠道必须启用")
        for other in db.scalars(
            select(ProviderConfig).where(ProviderConfig.project_id == ctx.project.id)
        ):
            if (
                other.id != row.id
                and other.config.get("modality") == data.modality
                and other.config.get("is_default")
            ):
                other.config = {
                    **other.config,
                    "is_default": False,
                    "revision": other.config.get("revision", 1) + 1,
                }
    old_name = row.config.get("display_name", row.provider_name)
    old_modality = row.config.get("modality")
    same_model = (
        row.config.get("model") == data.model
        and row.config.get("protocol") == data.protocol
        and row.config.get("sdk_provider", "openai") == data.sdk_provider
    )
    row.kind = "llm" if data.modality == "text" else "generation"
    row.enabled = data.enabled
    # Explicit allowlist: credentials never enter config, API output, logs or task snapshots.
    row.config = {
        **{k: v for k, v in row.config.items() if k in ("rate", "quality", "size", "features")},
        "channel_id": str(ch.id),
        "display_name": data.name,
        "model": data.model,
        "protocol": minimax_models.video_protocol(ch.endpoint, data.model, data.protocol),
        "sdk_provider": data.sdk_provider,
        "modality": data.modality,
        "input_capabilities": (
            data.input_capabilities.model_dump(mode="json")
            if data.input_capabilities
            else row.config.get("input_capabilities", InputCapabilities().model_dump())
            if same_model
            else InputCapabilities().model_dump()
        )
        if data.modality == "text"
        else None,
        "is_default": data.is_default,
        "revision": row.config.get("revision", 0) + 1,
        "max_reference_images": data.max_reference_images,
        "max_reference_videos": data.max_reference_videos,
        "mask_editing": data.mask_editing,
        "max_video_seconds": data.max_video_seconds,
        "stream": row.config.get("stream", False),
        "param_schema": {k: {"enum": list(dict.fromkeys(v))} for k, v in data.parameters.items()},
        "price": data.price.model_dump(mode="json"),
    }
    row.endpoint = None
    row.credentials_encrypted = None
    # Keep a simple, same-name default in sync; preserve intentionally named or multi-route groups.
    for linked in db.scalars(
        select(CreativeModel).where(CreativeModel.project_id == ctx.project.id).with_for_update()
    ):
        routes = linked.definition.get("routes", [])
        if (
            len(routes) == 1
            and str(routes[0].get("profile_id")) == str(row.id)
            and linked.name == old_name
            and linked.modality == old_modality
        ):
            linked.name = data.name
            if linked.modality != data.modality:
                # Changed capabilities invalidate the old group until it is configured again.
                linked.enabled = False
                linked.definition = {**linked.definition, "is_default": False}
            linked.revision += 1
    db.flush()
    audit.record(
        db,
        action="channel_model.save",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="provider_config",
        target_id=row.id,
        detail={
            "channel_id": str(ch.id),
            "protocol": data.protocol,
            "sdk_provider": data.sdk_provider,
            "model": data.model,
            "enabled": data.enabled,
        },
    )
    return row


def import_legacy(db, ctx):
    from app.core.config import settings

    lock_model_scope(db, ctx.project.id)
    count = 0
    for row in db.scalars(
        select(ProviderConfig).where(ProviderConfig.project_id == ctx.project.id).with_for_update()
    ):
        if row.config.get("channel_id") or row.provider_name not in PROTOCOLS:
            continue
        ch = ModelChannel(
            project_id=ctx.project.id,
            name=row.provider_name + " · 原配置",
            endpoint=row.endpoint
            or (
                settings.llm_base_url
                if row.kind == "llm"
                else "https://api.openai.com/v1"
                if row.provider_name == "gpt_image"
                else ""
            ),
            credentials_encrypted=row.credentials_encrypted
            or (
                encrypt(settings.llm_api_key)
                if row.kind == "llm" and settings.llm_api_key
                else None
            ),
            enabled=True,
        )
        db.add(ch)
        db.flush()
        row.config = {
            **row.config,
            "channel_id": str(ch.id),
            "protocol": row.provider_name,
            "display_name": row.config.get("model") or row.provider_name,
            "revision": 1,
        }
        row.endpoint, row.credentials_encrypted = None, None
        count += 1
    db.flush()
    audit.record(
        db,
        action="model_channel.import",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        detail={"imported": count},
    )
    return {"imported": count}


def resolved(db, row):
    c = row.config or {}
    if not c.get("channel_id"):
        return SimpleNamespace(
            id=row.id,
            provider_name=row.provider_name,
            endpoint=row.endpoint,
            credentials_encrypted=row.credentials_encrypted,
            config=minimax_models.video_profile(c, row.endpoint),
            enabled=row.enabled,
            channel_name="",
        )
    ch = channel(db, row.project_id, uuid.UUID(c["channel_id"]))
    return SimpleNamespace(
        id=row.id,
        provider_name=row.provider_name,
        endpoint=ch.endpoint,
        credentials_encrypted=ch.credentials_encrypted,
        config=minimax_models.video_profile(c, ch.endpoint),
        enabled=row.enabled and ch.enabled,
        channel_name=ch.name,
    )


def provider_view(db, row):
    from app.modules.generation.schemas import ProviderConfigOut

    result = ProviderConfigOut.model_validate(row).model_dump()
    r = resolved(db, row)
    result.update(
        config=r.config,
        enabled=r.enabled,
        endpoint=r.endpoint,
        display_name=row.config.get("display_name", row.provider_name),
        channel_name=r.channel_name,
    )
    return result


def selected_llm(db, project_id, name, user_id=None, *, for_request=True):
    """Resolve a text model without contacting its host when inspecting the catalog."""
    if name.startswith("personal_"):
        from app.modules.generation.personal import text_config

        return text_config(db, name, user_id, for_request=for_request)
    from app.modules.generation import model_catalog as catalog

    if name.startswith(catalog.PREFIX):
        try:
            model_id = uuid.UUID(name[len(catalog.PREFIX) :])
        except ValueError:
            raise NotFound("文字模型不存在") from None
        model = catalog.get_model(db, project_id, model_id, allow_global=True)
        if model.modality != "text" or not model.enabled:
            raise CapabilityUnsupported("请选择已启用的文字模型")
        candidates, _ = catalog.all_candidates(db, model)
        if not candidates:
            raise CapabilityUnsupported("所选文字模型没有可用连接")
        row = db.get(ProviderConfig, uuid.UUID(catalog.ordered(candidates)[0]["profile_id"]))
    else:
        row = db.scalar(
            select(ProviderConfig).where(
                ProviderConfig.project_id == (None if name.startswith("platform_") else project_id),
                ProviderConfig.provider_name == name,
            )
        )
    if not row or row.kind != "llm":
        raise NotFound("文字模型不属于当前项目")
    value = resolved(db, row)
    if not value.enabled or not value.endpoint or not value.credentials_encrypted:
        raise CapabilityUnsupported("所选文字模型尚未连接或已停用")
    return value


def llm_config(db, project_id):
    from app.modules.generation import model_catalog as catalog

    logical = [
        m
        for m in catalog.rows(db, project_id)
        if m.modality == "text" and m.definition.get("is_default")
    ]
    if logical:
        model = logical[0]
        if not model.enabled:
            raise CapabilityUnsupported("默认文字模型已停用，请在创作模型中调整")
        candidates, _ = catalog.all_candidates(db, model)
        if not candidates:
            raise CapabilityUnsupported("默认文字模型没有可用线路，请检查渠道配置")
        selected = catalog.ordered(candidates)[0]
        row = db.get(ProviderConfig, uuid.UUID(selected["profile_id"]))
        return resolved(db, row)
    rows = list(
        db.scalars(
            select(ProviderConfig)
            .where(
                ProviderConfig.project_id == project_id,
                ProviderConfig.kind == "llm",
            )
            .order_by(ProviderConfig.created_at, ProviderConfig.id)
        )
    )
    rows.sort(key=lambda p: not p.config.get("is_default", False))
    for row in rows:
        r = resolved(db, row)
        if r.enabled:
            if row.config.get("channel_id") and (not r.endpoint or not r.credentials_encrypted):
                raise CapabilityUnsupported("默认文字模型的渠道缺少地址或密钥，请在项目设置中补全")
            return r
    if any(row.config.get("channel_id") for row in rows):
        raise CapabilityUnsupported("项目文字模型或渠道已停用，请在项目设置中启用后再使用")
    return llm_config(db, None) if project_id is not None else None


def model_snapshot(db, project_id, name):
    row = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.project_id == (None if name.startswith("platform_") else project_id),
            ProviderConfig.provider_name == name,
        )
    )
    if row and row.config.get("channel_id"):
        return {"model_profile": copy.deepcopy(resolved(db, row).config)}
    return {}


def discover(db, ctx, channel_id, purpose=None):

    ch = channel(db, ctx.project.id, channel_id)
    return discover_connection(ch, discovery_protocol(db, ch.provider_template_id), purpose=purpose)


def check_connection(db, ctx, data, channel_id=None):
    ch = channel(db, ctx.project.id, channel_id) if channel_id else None
    return check_channel_connection(db, ch, data)


def check_channel_connection(db, ch, data):
    require_matching_credential_origin(ch, data)
    credential = (
        encrypt(data.token)
        if data.token
        else (ch.credentials_encrypted if ch and not data.clear_token else None)
    )
    return discover_connection(
        SimpleNamespace(endpoint=data.endpoint, credentials_encrypted=credential),
        discovery_protocol(
            db, data.provider_template_id or (ch.provider_template_id if ch else None)
        ),
    )


def discovery_protocol(db, template_id):
    from app.modules.generation.provider_templates import listing

    template = next((t for t in listing(db, True) if t["id"] == template_id), None)
    return template.get("discovery_protocol", "openai") if template else "openai"


def discover_connection(ch, protocol="openai", *, purpose=None):
    from app.core.crypto import decrypt

    if not ch.endpoint or not ch.credentials_encrypted:
        raise CapabilityUnsupported("请先保存服务地址和密钥")
    if protocol == "manual":
        raise CapabilityUnsupported("此服务不提供模型目录，请按供应商文档手动填写模型 ID")
    token = decrypt(ch.credentials_encrypted)
    template_id = getattr(ch, "provider_template_id", None) or getattr(ch, "preset_id", None)
    endpoint = ch.endpoint.rstrip("/")
    official_minimax = protocol == "openai" and endpoint in minimax_models.OFFICIAL_ENDPOINTS
    minimax_catalog = official_minimax or template_id == "minimax"
    headers = {"Authorization": "Bearer " + token}
    if protocol == "anthropic":
        endpoint = endpoint if endpoint.endswith("/v1") else endpoint + "/v1"
        headers = {"x-api-key": token, "anthropic-version": "2023-06-01"}
    elif protocol == "gemini":
        endpoint = endpoint if endpoint.endswith(("/v1beta", "/v1")) else endpoint + "/v1beta"
        headers = {"x-goog-api-key": token}
    try:
        # Never follow redirects with a stored credential; connection test does not generate media.
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            with client.stream(
                "GET",
                endpoint + "/models",
                params={"output_modalities": purpose}
                if protocol == "openrouter" and purpose
                else None,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    raise ProviderError(
                        f"模型目录读取失败（HTTP {response.status_code}），可检查地址或手动添加模型"
                    )
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise ProviderError("模型目录过大，请手动添加模型")
        import json

        payload = json.loads(data)
        if minimax_catalog and (
            payload.get("error") or (payload.get("base_resp") or {}).get("status_code", 0) != 0
        ):
            raise ProviderError("MiniMax 模型目录读取失败，请检查密钥和账号权限")
        models = payload.get("models" if protocol == "gemini" else "data")
        if protocol == "gemini" and isinstance(models, list):
            models = [
                {"id": x["name"].removeprefix("models/"), "name": x.get("displayName")}
                for x in models
                if isinstance(x, dict) and isinstance(x.get("name"), str)
            ]
        if not isinstance(models, list):
            raise ValueError()
        details = {}
        for entry in models:
            if not isinstance(entry, dict):
                continue
            code = entry.get("id")
            if not isinstance(code, str) or not 0 < len(code) <= 128:
                continue
            item = {"id": code}
            title = entry.get("name") or entry.get("display_name")
            if isinstance(title, str) and title.strip() and title != code:
                item["name"] = title.strip()[:160]
            architecture = entry.get("architecture")
            outputs = (
                architecture.get("output_modalities") if isinstance(architecture, dict) else None
            )
            if isinstance(outputs, list) and outputs:
                item["outputs"] = [x for x in outputs if isinstance(x, str)]
            elif minimax_catalog:
                output = minimax_models.MODEL_OUTPUTS.get(code)
                if output:
                    item["outputs"] = [output]
            if official_minimax and code in minimax_models.MEDIA_NOTES:
                item["note"] = minimax_models.MEDIA_NOTES[code]
            details[code] = item
        ids = sorted(details)
        if minimax_catalog and purpose:
            ids = [code for code in ids if purpose in details[code].get("outputs", [])]
        return {
            "models": ids[:1000],
            "details": [details[k] for k in ids[:1000]],
            "documented_models": minimax_models.documented_media(details, purpose)
            if official_minimax
            else [],
            "documentation_url": minimax_models.DOCUMENTATION_URL if official_minimax else None,
            "truncated": len(ids) > 1000
            or bool(payload.get("nextPageToken") or payload.get("has_more")),
            "message": "目录连接成功；生成能力仍需按协议配置，不代表所有模型均可生成图片或视频",
        }
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("无法读取模型目录，请检查地址、密钥或手动添加模型") from exc


def remove_channel(db, ctx, channel_id, revision):
    lock_model_scope(db, ctx.project.id)
    row = channel(db, ctx.project.id, channel_id, True)
    if row.revision != revision:
        raise Conflict("连接已更新，请刷新后重新删除")
    profiles = [
        p
        for p in db.scalars(
            select(ProviderConfig)
            .where(ProviderConfig.project_id == ctx.project.id)
            .with_for_update()
        )
        if p.config.get("channel_id") == str(row.id)
    ]
    ids = {str(p.id) for p in profiles}
    names = {p.provider_name for p in profiles}
    for job in db.scalars(
        select(GenerationJob).where(
            (GenerationJob.project_id == ctx.project.id) if ctx.project.id is not None else True,
            GenerationJob.status.in_(["pending", "submitted", "running"]),
        )
    ):
        candidates = job.input_snapshot.get("routing", {}).get("candidates", [])
        if job.provider in names or any(r.get("channel_id") == str(row.id) for r in candidates):
            raise Conflict("此服务仍有生成任务正在执行，请等待完成或取消任务后再删除")
    for model in db.scalars(
        select(CreativeModel).where(CreativeModel.project_id == ctx.project.id).with_for_update()
    ):
        previous = model.definition.get("routes", [])
        remaining = [r for r in previous if str(r.get("profile_id")) not in ids]
        if len(remaining) != len(previous):
            model.definition = {**model.definition, "routes": remaining}
            model.revision += 1
            if not any(r.get("enabled", True) for r in remaining):
                model.enabled = False
                model.definition = {**model.definition, "is_default": False}
    for profile in profiles:
        db.delete(profile)
    db.delete(row)
    audit.record(
        db,
        action="model_channel.remove",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="model_channel",
        target_id=channel_id,
        detail={"model_count": len(profiles)},
    )
    db.flush()
