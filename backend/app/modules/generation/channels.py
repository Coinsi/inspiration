"""Project-scoped channels and model profiles, reusing stable provider/job identities."""

import copy
import uuid
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from app.core import audit
from app.core.crypto import encrypt
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, ProviderError
from app.models.generation import ModelChannel, ProviderConfig
from app.models.identity import Project

PROTOCOLS = {
    "gpt_image": {"name": "OpenAI Images 兼容", "types": ["image"]},
    "openai_video": {"name": "OpenAI Videos 兼容", "types": ["video"]},
    "cloud_llm": {"name": "OpenAI Chat 兼容", "types": ["text"]},
    "jimeng": {"name": "旧版自定义 /submit · /status", "types": ["image", "video"]},
    "mock": {"name": "离线测试", "types": ["image", "video"]},
}


class ChannelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(default="", max_length=512)
    token: str | None = Field(default=None, max_length=8192)
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
    protocol: Literal["gpt_image", "openai_video", "cloud_llm", "jimeng", "mock"]
    modality: Literal["image", "video", "text"]
    enabled: bool = True
    is_default: bool = False
    revision: int | None = None
    request_id: uuid.UUID | None = None
    max_reference_images: int = Field(default=0, ge=0, le=16)
    mask_editing: bool = False
    max_video_seconds: float = Field(default=10, ge=1, le=120, allow_inf_nan=False)
    parameters: dict[str, list[str | int]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def supported(self):
        if self.is_default and self.modality != "text":
            raise ValueError("目前默认模型用于文字创作；图片和视频在创作时选择")
        if self.modality not in PROTOCOLS[self.protocol]["types"]:
            raise ValueError("协议不支持所选模型类型")
        if self.mask_editing and (self.protocol != "gpt_image" or not self.max_reference_images):
            raise ValueError("蒙版编辑需要支持参考图的 Images 协议")
        if self.protocol == "openai_video" and self.max_reference_images > 1:
            raise ValueError("Videos 协议最多接收一张起始参考图")
        if self.modality == "text" and (self.parameters or self.max_reference_images):
            raise ValueError("文字模型不使用生成参数与参考图上限")
        allowed = (
            {"size", "quality"}
            if self.modality == "image"
            else {"duration", "size", "aspect_ratio", "resolution"}
        )
        if self.protocol == "openai_video":
            allowed = {"duration", "size"}
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


def public_model(row):
    c = row.config or {}
    return {
        "id": row.id,
        "provider_name": row.provider_name,
        "name": c.get("display_name", row.provider_name),
        "model": c.get("model", ""),
        "protocol": c.get("protocol", row.provider_name),
        "modality": c.get("modality", "text" if row.kind == "llm" else "image"),
        "enabled": row.enabled,
        "is_default": bool(c.get("is_default")),
        "revision": c.get("revision", 1),
        "max_reference_images": c.get("max_reference_images", 0),
        "mask_editing": c.get("mask_editing", False),
        "max_video_seconds": c.get("max_video_seconds", 10),
        "parameters": {k: v.get("enum", []) for k, v in c.get("param_schema", {}).items()},
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
                "enabled": r.enabled,
                "has_token": bool(r.credentials_encrypted),
                "revision": r.revision,
                "models": [
                    public_model(p) for p in models if p.config.get("channel_id") == str(r.id)
                ],
            }
            for r in rows
        ],
        "legacy_count": sum(
            not p.config.get("channel_id") and p.provider_name in PROTOCOLS for p in models
        ),
        "protocols": PROTOCOLS,
    }


def save_channel(db, ctx, data, channel_id=None):
    # Serialize creates and defaults within a project; request identities make retries safe.
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
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
    row.name, row.endpoint, row.enabled = data.name, data.endpoint, data.enabled
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


def save_model(db, ctx, channel_id, data, model_id=None):
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
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
            provider_name="model_" + uuid.uuid4().hex,
            config={},
        )
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
    row.kind = "llm" if data.modality == "text" else "generation"
    row.enabled = data.enabled
    # Explicit allowlist: credentials never enter config, API output, logs or task snapshots.
    row.config = {
        **{k: v for k, v in row.config.items() if k in ("rate", "quality", "size", "features")},
        "channel_id": str(ch.id),
        "display_name": data.name,
        "model": data.model,
        "protocol": data.protocol,
        "modality": data.modality,
        "is_default": data.is_default,
        "revision": row.config.get("revision", 0) + 1,
        "max_reference_images": data.max_reference_images,
        "mask_editing": data.mask_editing,
        "max_video_seconds": data.max_video_seconds,
        "stream": row.config.get("stream", False),
        "param_schema": {k: {"enum": list(dict.fromkeys(v))} for k, v in data.parameters.items()},
    }
    row.endpoint = None
    row.credentials_encrypted = None
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
            "model": data.model,
            "enabled": data.enabled,
        },
    )
    return row


def import_legacy(db, ctx):
    from app.core.config import settings

    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
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
            endpoint=row.endpoint,
            credentials_encrypted=row.credentials_encrypted,
            config=c,
            enabled=row.enabled,
            channel_name="",
        )
    ch = channel(db, row.project_id, uuid.UUID(c["channel_id"]))
    return SimpleNamespace(
        endpoint=ch.endpoint,
        credentials_encrypted=ch.credentials_encrypted,
        config=c,
        enabled=row.enabled and ch.enabled,
        channel_name=ch.name,
    )


def provider_view(db, row):
    from app.modules.generation.schemas import ProviderConfigOut

    result = ProviderConfigOut.model_validate(row).model_dump()
    r = resolved(db, row)
    result.update(
        enabled=r.enabled,
        endpoint=r.endpoint,
        display_name=row.config.get("display_name", row.provider_name),
        channel_name=r.channel_name,
    )
    return result


def llm_config(db, project_id):
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
    return None


def model_snapshot(db, project_id, name):
    row = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.project_id == project_id, ProviderConfig.provider_name == name
        )
    )
    if row and row.config.get("channel_id"):
        return {"model_profile": copy.deepcopy(row.config)}
    return {}


def discover(db, ctx, channel_id):
    from app.core.crypto import decrypt

    ch = channel(db, ctx.project.id, channel_id)
    if not ch.endpoint or not ch.credentials_encrypted:
        raise CapabilityUnsupported("请先保存服务地址和密钥")
    try:
        # Never follow redirects with a stored credential; connection test does not generate media.
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            with client.stream(
                "GET",
                ch.endpoint + "/models",
                headers={"Authorization": "Bearer " + decrypt(ch.credentials_encrypted)},
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
        models = payload.get("data")
        if not isinstance(models, list):
            raise ValueError()
        ids = sorted(
            {
                x["id"]
                for x in models
                if isinstance(x, dict) and isinstance(x.get("id"), str) and 0 < len(x["id"]) <= 128
            }
        )
        return {
            "models": ids[:1000],
            "truncated": len(ids) > 1000,
            "message": "目录连接成功；生成能力仍需按协议配置，不代表所有模型均可生成图片或视频",
        }
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("无法读取模型目录，请检查地址、密钥或手动添加模型") from exc
