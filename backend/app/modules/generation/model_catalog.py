"""Creator models route to project-owned channel profiles, never to user supplied URLs."""

import copy
import hashlib
import secrets
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.adapters.contracts import Capabilities, CostEstimate
from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.generation import CreativeModel, ModelChannel, ProviderConfig
from app.models.identity import Project

PREFIX = "creative_"


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["unconfigured", "per_request", "per_second"] = "unconfigured"
    amount: Decimal = Field(default=0, ge=0, le=1000000, decimal_places=4)

    def quote(self, req):
        if self.mode == "unconfigured":
            return None
        quantity = req.count
        if self.mode == "per_second":
            duration = req.provider_params.get("duration")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or duration <= 0
            ):
                raise CapabilityUnsupported("按秒计价的模型需要明确选择视频时长")
            quantity *= Decimal(str(duration))
        return float((self.amount * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class RouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: uuid.UUID
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    weight: int = Field(default=100, ge=1, le=10000)


class CreativeModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    modality: Literal["image", "video", "text"]
    enabled: bool = True
    is_default: bool = False
    revision: int | None = None
    request_id: uuid.UUID | None = None
    routes: list[RouteIn] = Field(min_length=1, max_length=30)
    price_policy: Literal["channel", "unified"] = "channel"
    price: Price = Field(default_factory=Price)

    @model_validator(mode="after")
    def validate_routes(self):
        if len({r.profile_id for r in self.routes}) != len(self.routes):
            raise ValueError("同一渠道模型只能绑定一次")
        if self.enabled and not any(r.enabled for r in self.routes):
            raise ValueError("启用模型至少需要一条启用的线路")
        if self.is_default and not self.enabled:
            raise ValueError("默认模型必须启用")
        if self.price.mode == "per_second" and self.modality != "video":
            raise ValueError("按秒计价仅用于视频")
        if self.modality == "text" and self.price.mode != "unconfigured":
            raise ValueError("文字用量计价尚未接入，请保留未配置")
        return self


def get_model(db, project_id, identity, lock=False):
    try:
        ident = uuid.UUID(str(identity).removeprefix(PREFIX))
    except ValueError:
        raise NotFound("创作模型不存在") from None
    query = select(CreativeModel).where(
        CreativeModel.id == ident, CreativeModel.project_id == project_id
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise NotFound("创作模型不存在")
    return row


def rows(db, project_id):
    return list(
        db.scalars(
            select(CreativeModel)
            .where(CreativeModel.project_id == project_id)
            .order_by(CreativeModel.created_at, CreativeModel.id)
        )
    )


def route_profile(db, project_id, route):
    row = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.id == uuid.UUID(str(route["profile_id"])),
            ProviderConfig.project_id == project_id,
        )
    )
    if not row or not row.config.get("channel_id"):
        return None, None, "渠道模型已不存在"
    channel = db.scalar(
        select(ModelChannel).where(
            ModelChannel.id == uuid.UUID(row.config["channel_id"]),
            ModelChannel.project_id == project_id,
        )
    )
    if not channel:
        return row, None, "渠道已不存在"
    reason = ""
    if not route.get("enabled", True):
        reason = "线路已停用"
    elif not channel.enabled or not row.enabled:
        reason = "渠道或模型已停用"
    elif row.config.get("protocol") != "mock" and (
        not channel.endpoint or not channel.credentials_encrypted
    ):
        reason = "渠道尚未配置地址或密钥"
    return row, channel, reason


def public(db, row, admin=False):
    definition = row.definition
    routes = []
    for route in definition.get("routes", []):
        p, ch, reason = route_profile(db, row.project_id, route)
        if p and p.config.get("modality") != row.modality:
            reason = "渠道模型类型已改变"
        routes.append(
            {
                **route,
                "available": not reason,
                "reason": reason,
                "channel_name": ch.name if ch else "已移除渠道",
                "model_name": p.config.get("display_name", p.provider_name) if p else "已移除模型",
                "model": p.config.get("model", "") if p else "",
            }
        )
    result = {
        "id": row.id,
        "provider_name": PREFIX + row.id.hex,
        "name": row.name,
        "modality": row.modality,
        "enabled": row.enabled,
        "revision": row.revision,
        "description": definition.get("description", ""),
        "is_default": definition.get("is_default", False),
        "price_policy": definition.get("price_policy", "channel"),
        "price": definition.get("price", {}),
        "route_count": len(routes),
        "available_routes": sum(r["available"] for r in routes),
        "available": row.enabled and any(r["available"] for r in routes),
    }
    if admin:
        result["routes"] = routes
    return result


def save(db, ctx, data, model_id=None):
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    row = get_model(db, ctx.project.id, model_id, True) if model_id else None
    if row and row.revision != data.revision:
        raise Conflict("模型已被修改，请重新读取后保存")
    if not row and data.request_id:
        existing = db.get(CreativeModel, data.request_id)
        if existing:
            if existing.project_id != ctx.project.id:
                raise Conflict("创建标识已使用")
            return existing
    for route in data.routes:
        p, _, _ = route_profile(db, ctx.project.id, route.model_dump(mode="json"))
        if not p:
            raise NotFound("绑定的渠道模型不属于当前项目或已不存在")
        if p.config.get("modality") != data.modality:
            raise CapabilityUnsupported("线路与创作模型的类型不一致")
    if row and row.modality != data.modality:
        raise Conflict("已创建模型不能改变媒体类型，请新建模型")
    if not row:
        row = CreativeModel(
            id=data.request_id or uuid.uuid4(), project_id=ctx.project.id, revision=0
        )
        db.add(row)
    if data.is_default:
        for other in rows(db, ctx.project.id):
            if (
                other.id != row.id
                and other.modality == data.modality
                and other.definition.get("is_default")
            ):
                other.definition = {**other.definition, "is_default": False}
                other.revision += 1
    row.name, row.modality, row.enabled = data.name, data.modality, data.enabled
    row.revision += 1
    row.definition = data.model_dump(
        mode="json", exclude={"name", "modality", "enabled", "revision", "request_id"}
    )
    db.flush()
    audit.record(
        db,
        action="creative_model.save",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="creative_model",
        target_id=row.id,
        detail={"revision": row.revision, "routes": len(data.routes)},
    )
    return row


def import_profiles(db, ctx):
    """Explicit, idempotent adoption; never infer that equal names imply equal models."""
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    bound = {
        r["profile_id"] for m in rows(db, ctx.project.id) for r in m.definition.get("routes", [])
    }
    count = 0
    for p in db.scalars(
        select(ProviderConfig)
        .where(ProviderConfig.project_id == ctx.project.id)
        .order_by(ProviderConfig.created_at)
    ):
        if not p.config.get("channel_id") or str(p.id) in bound:
            continue
        modality = p.config.get("modality")
        if modality not in ("image", "video", "text"):
            continue
        save(
            db,
            ctx,
            CreativeModelIn(
                name=p.config.get("display_name", p.provider_name),
                modality=modality,
                enabled=p.enabled,
                routes=[RouteIn(profile_id=p.id)],
                is_default=bool(p.config.get("is_default")) and p.enabled,
            ),
        )
        count += 1
    return {"imported": count}


class DefaultModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    source: Literal["model", "profile"]


def select_default(db, ctx, data):
    """Choose a default without making creators manually assemble a route."""
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    if data.source == "model":
        row = get_model(db, ctx.project.id, data.id, True)
    else:
        profile, channel, reason = route_profile(db, ctx.project.id, {"profile_id": data.id})
        if not profile or not channel:
            raise NotFound("模型不属于当前项目或已被移除")
        if reason:
            raise CapabilityUnsupported(reason)
        row = next(
            (
                m
                for m in rows(db, ctx.project.id)
                if any(r["profile_id"] == str(data.id) for r in m.definition.get("routes", []))
            ),
            None,
        )
        if not row:
            modality = profile.config.get("modality")
            if modality not in ("image", "video", "text"):
                raise CapabilityUnsupported("请先在服务连接中完善此模型的类型")
            row = save(
                db,
                ctx,
                CreativeModelIn(
                    name=profile.config.get("display_name", profile.provider_name),
                    modality=modality,
                    routes=[RouteIn(profile_id=profile.id)],
                ),
            )
    if not public(db, row)["available"]:
        raise CapabilityUnsupported("此模型尚未连接或已停用，请先检查服务连接")
    value = CreativeModelIn(
        **{**row.definition, "is_default": True},
        name=row.name,
        modality=row.modality,
        revision=row.revision,
    )
    return save(db, ctx, value, row.id)


def all_candidates(db, row):
    from app.modules.generation.service import _provider_instance

    candidates, rejected = [], []
    for route in row.definition.get("routes", []):
        p, ch, reason = route_profile(db, row.project_id, route)
        if p and p.config.get("modality") != row.modality:
            reason = "渠道模型类型已改变"
        caps = None
        if not reason and row.modality != "text":
            try:
                caps = _provider_instance(db, row.project_id, p.provider_name).capabilities()
            except CapabilityUnsupported as exc:
                reason = exc.message
        if reason:
            rejected.append({"profile_id": route["profile_id"], "reason": reason})
            continue
        candidates.append(
            {
                **route,
                "provider_name": p.provider_name,
                "channel_name": ch.name,
                "channel_id": str(ch.id),
                "channel_revision": ch.revision,
                "endpoint_hash": hashlib.sha256(ch.endpoint.encode()).hexdigest(),
                "model_name": p.config.get("display_name", p.provider_name),
                "model_profile": copy.deepcopy(p.config),
                "capabilities": caps.model_dump(mode="json") if caps else None,
            }
        )
    return candidates, rejected


def mismatch(req, caps):
    if req.request_type not in caps.modalities:
        return "不支持该媒体类型"
    if req.mask and "inpaint" not in caps.features:
        return "不支持蒙版重绘"
    if req.references:
        if "reference" not in caps.features or len(req.references) > caps.max_reference_images:
            return "参考图片数量超出能力范围"
        for ref in req.references:
            if ref.role in ("first_frame", "last_frame") and ref.role not in caps.features:
                return "不支持首帧或尾帧输入"
    for key, value in req.provider_params.items():
        if value not in caps.param_schema.get(key, {}).get("enum", []):
            return f"不支持参数 {key}={value}"
    return ""


def ordered(candidates):
    pending, result = list(candidates), []
    while pending:
        priority = max(c["priority"] for c in pending)
        group = [c for c in pending if c["priority"] == priority]
        pick = secrets.randbelow(sum(c["weight"] for c in group))
        for candidate in group:
            pick -= candidate["weight"]
            if pick < 0:
                result.append(candidate)
                pending.remove(candidate)
                break
    return result


def plan(db, row, req):
    if row.modality == "text":
        raise CapabilityUnsupported("文字模型通过默认文字创作入口使用，不支持图片或视频生成请求")
    if not row.enabled:
        raise CapabilityUnsupported("创作模型已停用")
    candidates, rejected = all_candidates(db, row)
    eligible = []
    for c in candidates:
        caps = Capabilities(**c["capabilities"])
        reason = mismatch(req, caps)
        if c["model_profile"].get("protocol") == "openai_video" and req.count != 1:
            reason = "该视频协议每次只生成一个视频"
        if not reason:
            from app.adapters.contracts import generation_registry

            try:
                generation_registry.create(
                    c["model_profile"]["protocol"], config=c["model_profile"]
                ).validate_request(req)
            except CapabilityUnsupported as exc:
                reason = exc.message
        if reason:
            rejected.append({"profile_id": c["profile_id"], "reason": reason})
            continue
        price = Price(
            **(
                row.definition.get("price", {})
                if row.definition.get("price_policy") == "unified"
                else c["model_profile"].get("price", {})
            )
        )
        try:
            amount = price.quote(req)
        except CapabilityUnsupported as exc:
            rejected.append({"profile_id": c["profile_id"], "reason": exc.message})
            continue
        eligible.append({**c, "cost_points": amount})
    if not eligible:
        raise CapabilityUnsupported(
            "当前模型没有能满足这组输入与参数的可用线路", {"routes": rejected}
        )
    return {
        "model_id": str(row.id),
        "name": row.name,
        "revision": row.revision,
        "candidates": ordered(eligible),
        "rejected": rejected,
    }


def aggregate(db, row):
    candidates, _ = all_candidates(db, row)
    caps = Capabilities(
        modalities={row.modality} if row.modality != "text" else set(), strict_parameters=True
    )
    for item in candidates:
        if not item["capabilities"]:
            continue
        c = Capabilities(**item["capabilities"])
        caps.features |= c.features
        caps.max_reference_images = max(caps.max_reference_images, c.max_reference_images)
        caps.max_video_seconds = max(caps.max_video_seconds or 0, c.max_video_seconds or 0)
        for key, spec in c.param_schema.items():
            current = caps.param_schema.setdefault(key, {"enum": []})["enum"]
            current.extend(v for v in spec.get("enum", []) if v not in current)
    return caps


def estimate_plan(route_plan):
    amounts = [c["cost_points"] for c in route_plan["candidates"]]
    return CostEstimate(
        points=max((a or 0 for a in amounts), default=0),
        detail={
            "pricing": "configured" if all(a is not None for a in amounts) else "unconfigured",
            "min_points": min((a or 0 for a in amounts), default=0),
            "max_points": max((a or 0 for a in amounts), default=0),
            "message": "按可用线路最高积分预留，成功后按实际线路结算；积分不是供应商账单"
            if all(a is not None for a in amounts)
            else "有线路未配置积分，实际费用以上游账单为准",
            "model_name": route_plan["name"],
            "available_routes": len(amounts),
        },
    )
