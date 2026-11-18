"""The shared channel editor, backed by the signed-in user's existing private services."""

import uuid
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.crypto import encrypt
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.identity import User
from app.models.personal_model import PersonalModelService
from app.modules.generation import channels, minimax_models, personal, presets
from app.modules.generation.personal_endpoints import validate_endpoint
from app.modules.generation.provider_templates import listing

router = APIRouter(prefix="/me/model-channels", tags=["personal-model-services"])
DB = personal.DB
USER = personal.USER


def template_id(row):
    return row.provider_template_id or (row.preset_id if row.preset_id in presets.PRESETS else None)


def active_service(db, user_id, service_id, *, lock=False):
    row = personal.owned(db, user_id, service_id, lock=lock)
    if not row.enabled:
        raise NotFound("个人模型服务已删除")
    return row


def public_model(row, key):
    spec = row.models[key]
    model = channels.public_model(
        SimpleNamespace(
            id=key,
            provider_name=personal.provider_name(row.id, key),
            enabled=spec.get("enabled", True),
            kind="llm" if spec["modality"] == "text" else "generation",
            config={
                **personal.profile(spec, row.endpoint),
                "revision": row.revision,
                "is_default": False,
            },
        )
    )
    model["verification"] = row.verification.get(key, {"status": "unverified"})
    return model


@router.get("")
def catalog(user=USER, db=DB):
    rows = db.scalars(
        select(PersonalModelService)
        .where(PersonalModelService.user_id == user.id, PersonalModelService.enabled.is_(True))
        .order_by(PersonalModelService.created_at)
    )
    return {
        "channels": [
            {
                "id": row.id,
                "name": row.name,
                "endpoint": row.endpoint,
                "provider_template_id": template_id(row),
                "known_outputs": minimax_models.MODEL_OUTPUTS
                if template_id(row) == "minimax"
                else {},
                "preset_id": row.preset_id,
                "has_token": bool(row.credentials_encrypted),
                "enabled": row.enabled,
                "revision": row.revision,
                "models": [public_model(row, key) for key in row.models],
            }
            for row in rows
        ],
        "protocols": channels.PROTOCOLS,
        "legacy_count": 0,
        "suggestions": {p["id"]: p["models"] for p in presets.catalog()},
    }


def validate_template(db, target, previous=None):
    if target and target != previous and not any(t["id"] == target for t in listing(db)):
        raise CapabilityUnsupported("服务商模板已停用或不存在，请重新选择")


def validate_connection(db, data, stored=None, *, resolve=False):
    if not data.endpoint:
        raise CapabilityUnsupported("请填写服务地址")
    if not data.enabled or data.clear_token:
        raise CapabilityUnsupported("如需移除个人密钥，请删除连接")
    if not data.token and not (stored and stored.credentials_encrypted):
        raise CapabilityUnsupported("首次连接需要填写自己的密钥")
    channels.require_matching_credential_origin(stored, data)
    validate_endpoint(db, data.endpoint, stored, resolve=resolve)


@router.post("")
def create(data: channels.ChannelIn, user=USER, db=DB):
    validate_connection(db, data)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    preset_id = "custom-user-" + (data.request_id or uuid.uuid4()).hex
    ident = uuid.uuid5(user.id, "personal-model-service:" + preset_id)
    existing = db.get(PersonalModelService, ident)
    if existing:
        if not existing.enabled:
            raise Conflict("此连接已经被删除，请重新创建")
        return {"id": existing.id}
    validate_template(db, data.provider_template_id)
    row = PersonalModelService(
        id=ident,
        user_id=user.id,
        preset_id=preset_id,
        provider_template_id=data.provider_template_id,
        name=data.name,
        endpoint=data.endpoint,
        credentials_encrypted=encrypt(data.token),
        enabled=True,
        revision=1,
        models={},
        verification={},
    )
    db.add(row)
    db.flush()
    return {"id": row.id}


@router.put("/{channel_id}")
def update(channel_id: uuid.UUID, data: channels.ChannelIn, user=USER, db=DB):
    row = active_service(db, user.id, channel_id, lock=True)
    if data.revision != row.revision:
        raise Conflict("个人连接已更新，请重新读取后保存")
    validate_connection(db, data, row)
    target = (
        data.provider_template_id
        if "provider_template_id" in data.model_fields_set
        else template_id(row)
    )
    validate_template(db, target, template_id(row))
    if (data.name, data.endpoint, target) == (
        row.name,
        row.endpoint,
        template_id(row),
    ) and not data.token:
        return {"id": row.id}
    if data.token or row.endpoint != data.endpoint:
        row.verification = {}
    if data.token:
        row.credentials_encrypted = encrypt(data.token)
    row.name, row.endpoint, row.provider_template_id = data.name, data.endpoint, target
    row.revision += 1
    db.flush()
    return {"id": row.id}


@router.post("/check")
def check(data: channels.ChannelIn, channel_id: uuid.UUID | None = None, user=USER, db=DB):
    row = active_service(db, user.id, channel_id) if channel_id else None
    validate_connection(db, data, row, resolve=True)
    result = channels.check_channel_connection(db, row, data)
    return {"message": "已使用个人密钥读取模型目录", "model_count": len(result["models"])}


@router.post("/{channel_id}/discover")
def discover(
    channel_id: uuid.UUID,
    purpose: Literal["text", "image", "video"] | None = None,
    user=USER,
    db=DB,
):
    row = active_service(db, user.id, channel_id)
    validate_endpoint(db, row.endpoint, row, resolve=True)
    return channels.discover_connection(
        row,
        channels.discovery_protocol(db, template_id(row)),
        purpose=purpose,
    )


def write_model(db, row, key, data, *, editing):
    if data.is_default or data.price.mode != "unconfigured":
        raise CapabilityUnsupported("个人模型不设置项目默认模型或项目积分价格")
    previous = row.models.get(key)
    if editing:
        if previous is None:
            raise NotFound("个人连接中没有此模型")
        if data.revision != row.revision:
            raise Conflict("个人模型已更新，请重新打开后保存")
    elif previous is not None:
        return public_model(row, key)

    channels.validate_model_output(data, template_id(row))
    identity = (data.model, data.modality)
    if not previous or (previous["model"].strip(), previous["modality"]) != identity:
        if any(
            (spec["model"].strip(), spec["modality"]) == identity
            for other, spec in row.models.items()
            if other != key
        ):
            raise Conflict("这个模型已配置到当前用途，请编辑已有配置")
    spec = data.model_dump(
        mode="json", exclude={"request_id", "revision", "is_default", "price"}, exclude_none=True
    )
    spec["protocol"] = minimax_models.video_protocol(row.endpoint, data.model, data.protocol)
    # Older presets omit defaults. Opening and saving them must not invalidate in-flight jobs.
    if previous is not None:
        current = public_model(row, key)
        if all(current.get(k) == v for k, v in spec.items()):
            return current
    row.models = {**row.models, key: spec}
    row.verification = {k: v for k, v in row.verification.items() if k != key}
    row.revision += 1
    db.flush()
    return public_model(row, key)


@router.post("/{channel_id}/models")
def add_model(channel_id: uuid.UUID, data: channels.ModelIn, user=USER, db=DB):
    row = active_service(db, user.id, channel_id, lock=True)
    key = personal.compact_id(data.request_id or uuid.uuid4())
    return write_model(db, row, key, data, editing=False)


@router.put("/{channel_id}/models/{key}")
def update_model(channel_id: uuid.UUID, key: str, data: channels.ModelIn, user=USER, db=DB):
    row = active_service(db, user.id, channel_id, lock=True)
    return write_model(db, row, key, data, editing=True)


@router.delete("/{channel_id}", status_code=204)
def remove(channel_id: uuid.UUID, revision: int = Query(ge=1), user=USER, db=DB):
    personal.remove_service(channel_id, revision, user, db)
