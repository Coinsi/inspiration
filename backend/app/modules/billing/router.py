"""User checkout and super-admin payment operations."""

import ipaddress
import re
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, ProviderError
from app.core.platform_permissions import super_admin
from app.models.billing import (
    BillingOrder,
    BillingPack,
    BillingProvider,
    BillingRefund,
    BillingSettings,
    CreditAccount,
    CreditAdjustment,
    CreditEntry,
    CreditReservation,
    MembershipPlan,
    MembershipTerm,
)
from app.models.generation import GenerationJob
from app.models.identity import User
from app.modules.billing import memberships, orders, refunds
from app.modules.billing import wallet as credit_wallet
from app.modules.billing.providers import provider_catalog, validate_provider_config

router = APIRouter(tags=["billing"])
DB = Annotated[Session, Depends(get_db, scope="function")]
Current = Annotated[User, Depends(get_current_user)]
Super = Annotated[User, Depends(super_admin)]
Currency = Literal["cny", "usd", "eur", "gbp", "hkd", "sgd", "jpy"]
Name = Annotated[str, Field(min_length=1, max_length=100)]
Money = Annotated[int, Field(strict=True, gt=0, le=orders.MAX_AMOUNT_MINOR)]
Credits = Annotated[int, Field(strict=True, gt=0, le=10**10)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SettingsIn(Input):
    enabled: bool
    payment_required: bool
    public_origin: str = Field(max_length=300)
    checkout_minutes: int = Field(strict=True, ge=35, le=120)
    subscription_usd_to_cny_rate: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=6, allow_inf_nan=False
    )

    @field_validator("subscription_usd_to_cny_rate", mode="before")
    @classmethod
    def validate_subscription_rate(cls, value):
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or len(value) > 24
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,6})?", value.strip())
        ):
            raise ValueError("汇率须为普通十进制字符串，最多保留6位小数")
        rate = Decimal(value.strip())
        if rate > orders.MAX_CONVERSION_RATE:
            raise ValueError("汇率不能超过999999.999999")
        return rate if rate else None


class PackIn(Input):
    name: Name
    description: str = Field(default="", max_length=500)
    currency: Currency
    amount_minor: Money
    credits: Credits
    active: bool = True


class PlanIn(PackIn):
    features: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=12
    )
    original_amount_minor: Money | None = None
    duration: int = Field(strict=True, ge=1, le=3650)
    duration_unit: Literal["day", "week", "month"] = "month"
    sort_order: int = Field(default=0, strict=True, ge=-10_000, le=10_000)
    monthly_plan_id: uuid.UUID | None = None


class ProviderIn(Input):
    id: uuid.UUID | None = None
    name: Name
    kind: str = Field(min_length=1, max_length=32)
    currency: Currency
    active: bool = True
    allow_refunds: bool = True
    config: dict[str, SecretStr] = Field(max_length=24)


class ProviderEdit(Input):
    name: Name
    active: bool
    allow_refunds: bool = True


class CheckoutIn(Input):
    pack_id: uuid.UUID | None = None
    plan_id: uuid.UUID | None = None
    provider_id: uuid.UUID
    idempotency_key: uuid.UUID
    expected_amount_minor: Money | None = None


class RefundIn(Input):
    reason: Reason


class AdminRefundIn(RefundIn):
    amount_minor: Money
    idempotency_key: uuid.UUID


class ResolveRefundIn(RefundIn):
    outcome: Literal["succeeded", "failed"]


class ResolveReservationIn(Input):
    action: Literal["release", "settle"]
    reason: Reason
    units: int | None = Field(default=None, strict=True, gt=0, le=10**12)


class CreditAdjustmentIn(Input):
    idempotency_key: uuid.UUID
    operation: Literal["grant", "revoke"]
    points: str = Field(min_length=1, max_length=32)
    reason: Reason

    @field_validator("points")
    @classmethod
    def validate_points(cls, value):
        credit_wallet.exact_points_to_units(value, maximum=credit_wallet.MAX_ADJUSTMENT_UNITS)
        return value


class CreditSettingsIn(Input):
    welcome_points: str = Field(min_length=1, max_length=32)

    @field_validator("welcome_points")
    @classmethod
    def validate_welcome_points(cls, value):
        credit_wallet.exact_points_to_units(
            value, maximum=credit_wallet.MAX_WELCOME_UNITS, allow_zero=True
        )
        return value


def _pack_view(pack: BillingPack | MembershipPlan) -> dict:
    return {
        "id": pack.id,
        "name": pack.name,
        "description": pack.description,
        "currency": pack.currency,
        "amount_minor": pack.amount_minor,
        "credits": pack.credits,
        "active": pack.active,
    }


def _plan_view(plan: MembershipPlan) -> dict:
    return {
        **_pack_view(plan),
        "features": plan.features,
        "original_amount_minor": plan.original_amount_minor,
        "duration": plan.duration,
        "duration_unit": plan.duration_unit,
        "sort_order": plan.sort_order,
        "monthly_plan_id": plan.monthly_plan_id,
    }


def _plan_values(data: PlanIn, db: Session, current: MembershipPlan | None = None) -> dict:
    days = memberships.duration_days(data.duration, data.duration_unit)
    if data.original_amount_minor is not None and data.original_amount_minor < data.amount_minor:
        raise CapabilityUnsupported("参考价格不能低于售价")
    values = data.model_dump()
    # Legacy clients send a full plan payload without this newer optional field.
    # Only an explicit null clears a saved comparison link.
    if current is not None and "monthly_plan_id" not in data.model_fields_set:
        values["monthly_plan_id"] = current.monthly_plan_id
    monthly_id = values["monthly_plan_id"]
    if monthly_id is not None:
        if current is not None and monthly_id == current.id:
            raise CapabilityUnsupported("会员套餐不能关联自身作为月付方案")
        if days != 360:
            raise CapabilityUnsupported("仅360天的年付方案可以关联月付方案")
        monthly = db.get(MembershipPlan, monthly_id)
        if monthly is None:
            raise NotFound("关联的月付方案不存在")
        if memberships.duration_days(monthly.duration, monthly.duration_unit) != 30:
            raise CapabilityUnsupported("关联的月付方案有效期必须为30天")
        if monthly.currency != data.currency:
            raise CapabilityUnsupported("年付与月付方案必须使用相同币种")
        if data.credits < monthly.credits * 12:
            raise CapabilityUnsupported("年付额度不能少于月付额度的12倍")
    return values


def _provider_view(provider: BillingProvider) -> dict:
    return {
        "id": provider.id,
        "name": provider.name,
        "kind": provider.kind,
        "currency": provider.currency,
        "active": provider.active,
        "allow_refunds": provider.allow_refunds,
    }


def _settings_view(preferences: BillingSettings) -> dict:
    return {
        "enabled": preferences.enabled,
        "payment_required": preferences.payment_required,
        "public_origin": preferences.public_origin,
        "checkout_minutes": preferences.checkout_minutes,
        "subscription_usd_to_cny_rate": orders.rate_text(preferences.subscription_usd_to_cny_rate),
    }


def _audit(db, user, action, ident=None):
    audit.record(db, action=action, user_id=user.id, target_type="billing", target_id=ident)


@router.get("/billing/catalog")
def catalog(db: DB, _: Current):
    preferences = orders.config(db)
    if not preferences.enabled:
        return {"enabled": False, "packs": [], "plans": [], "providers": []}
    providers = list(
        db.scalars(
            select(BillingProvider).where(BillingProvider.active).order_by(BillingProvider.name)
        )
    )
    currencies = {provider.currency for provider in providers}
    packs = db.scalars(
        select(BillingPack)
        .where(BillingPack.active)
        .order_by(BillingPack.currency, BillingPack.amount_minor)
    )
    plans = db.scalars(
        select(MembershipPlan)
        .where(MembershipPlan.active)
        .order_by(MembershipPlan.sort_order, MembershipPlan.created_at, MembershipPlan.id)
    )
    plan_views = []
    for plan in plans:
        quotes = [
            quote
            for currency in currencies
            if (
                quote := orders.settlement_quote(
                    plan, currency, preferences.subscription_usd_to_cny_rate
                )
            )
        ]
        if not quotes:
            continue
        view = _plan_view(plan)
        converted = {
            quote.currency: quote.amount_minor
            for quote in quotes
            if quote.conversion_rate is not None
        }
        if converted:
            view["payment_amounts"] = converted
        plan_views.append(view)
    return {
        "enabled": True,
        "packs": [
            _pack_view(pack)
            for pack in packs
            if any(
                orders.settlement_quote(pack, currency, preferences.subscription_usd_to_cny_rate)
                for currency in currencies
            )
        ],
        "plans": plan_views,
        "providers": [_provider_view(provider) for provider in providers],
    }


@router.get("/billing/wallet")
def wallet(db: DB, user: Current):
    account = db.get(CreditAccount, user.id)
    entries = db.scalars(
        select(CreditEntry)
        .where(CreditEntry.user_id == user.id)
        .order_by(CreditEntry.created_at.desc(), CreditEntry.id.desc())
        .limit(50)
    )
    return {
        **credit_wallet.balance_view(account),
        "entries": [
            {
                "id": entry.id,
                "kind": entry.kind,
                "available_delta": entry.available_delta,
                "reserved_delta": entry.reserved_delta,
                "grant_available_delta": entry.grant_available_delta,
                "grant_reserved_delta": entry.grant_reserved_delta,
                "created_at": entry.created_at,
            }
            for entry in entries
        ],
    }


def _admin_credit_view(db: Session, user: User) -> dict:
    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        **credit_wallet.balance_view(db.get(CreditAccount, user.id)),
    }


def _adjustment_view(item: CreditAdjustment) -> dict:
    return {
        "id": item.id,
        "operation": item.operation,
        "units": item.units,
        "reason": item.reason,
        "actor_id": item.actor_id,
        "created_at": item.created_at,
    }


@router.get("/admin/billing/credits")
def admin_credits(
    db: DB,
    _: Super,
    search: Annotated[str, Query(max_length=320)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    query = select(User)
    if search.strip():
        query = query.where(
            or_(
                User.username == search.strip(),
                func.lower(func.trim(User.email)) == search.strip().lower(),
            )
        )
    users = db.scalars(query.order_by(User.created_at.desc(), User.id).limit(limit).offset(offset))
    return [_admin_credit_view(db, user) for user in users]


@router.get("/admin/billing/credits/{user_id}")
def admin_credit(db: DB, _: Super, user_id: uuid.UUID):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在")
    changes = db.scalars(
        select(CreditAdjustment)
        .where(CreditAdjustment.user_id == user_id)
        .order_by(CreditAdjustment.created_at.desc(), CreditAdjustment.id.desc())
        .limit(50)
    )
    return {
        **_admin_credit_view(db, user),
        "adjustments": [_adjustment_view(item) for item in changes],
    }


@router.post("/admin/billing/credits/{user_id}/adjustments")
def adjust_credit(db: DB, actor: Super, user_id: uuid.UUID, data: CreditAdjustmentIn):
    adjustment = credit_wallet.adjust(
        db,
        user_id,
        actor.id,
        data.idempotency_key,
        data.operation,
        credit_wallet.exact_points_to_units(
            data.points, maximum=credit_wallet.MAX_ADJUSTMENT_UNITS
        ),
        data.reason,
    )
    user = db.get(User, user_id)
    return {**_admin_credit_view(db, user), "adjustment": _adjustment_view(adjustment)}


def _credit_settings_view(preferences: BillingSettings | None) -> dict:
    units = preferences.welcome_credits if preferences else 0
    return {"welcome_points": credit_wallet.units_to_points(units), "welcome_credits": units}


@router.get("/admin/billing/credit-settings")
def credit_settings(db: DB, _: Super):
    return _credit_settings_view(db.get(BillingSettings, 1))


@router.patch("/admin/billing/credit-settings")
def update_credit_settings(db: DB, actor: Super, data: CreditSettingsIn):
    units = credit_wallet.exact_points_to_units(
        data.welcome_points, maximum=credit_wallet.MAX_WELCOME_UNITS, allow_zero=True
    )
    preferences = db.scalar(
        insert(BillingSettings)
        .values(id=1, welcome_credits=units)
        .on_conflict_do_update(
            index_elements=[BillingSettings.id],
            set_={"welcome_credits": units, "updated_at": func.now()},
        )
        .returning(BillingSettings)
        .execution_options(populate_existing=True)
    )
    audit.record(
        db,
        action="billing.credit_settings_updated",
        user_id=actor.id,
        target_type="billing",
        detail={"welcome_credits": units},
    )
    return _credit_settings_view(preferences)


@router.get("/billing/memberships")
def list_memberships(
    db: DB,
    user: Current,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return memberships.for_user(db, user.id, orders.now(), limit=limit, offset=offset)


@router.get("/billing/orders")
def list_orders(
    db: DB,
    user: Current,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows = db.scalars(
        select(BillingOrder)
        .where(BillingOrder.user_id == user.id)
        .order_by(BillingOrder.created_at.desc(), BillingOrder.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [orders.order_view(db, row, include_checkout=False) for row in rows]


@router.post("/billing/orders")
def create_order(data: CheckoutIn, request: Request, db: DB, user: Current):
    try:
        address = str(ipaddress.ip_address(request.client.host)) if request.client else None
    except ValueError:
        address = None
    user_agent = request.headers.get("user-agent", "").lower()
    row = orders.create_order(
        db,
        user.id,
        data.pack_id,
        data.provider_id,
        data.idempotency_key,
        plan_id=data.plan_id,
        client_ip=address,
        is_mobile=any(item in user_agent for item in ("mobile", "android", "iphone", "ipad")),
        expected_amount_minor=data.expected_amount_minor,
    )
    return orders.order_view(db, row)


@router.get("/billing/orders/{order_id}")
def get_order(order_id: uuid.UUID, db: DB, user: Current):
    return orders.order_view(db, orders.find_order(db, order_id, user_id=user.id))


@router.post("/billing/orders/{order_id}/check")
def check_order(order_id: uuid.UUID, db: DB, user: Current):
    orders.find_order(db, order_id, user_id=user.id)
    return orders.order_view(db, orders.check_order(db, order_id))


@router.post("/billing/orders/{order_id}/cancel")
def cancel_order(order_id: uuid.UUID, db: DB, user: Current):
    return orders.order_view(db, orders.cancel_order(db, order_id, user.id))


@router.post("/billing/orders/{order_id}/refund-request")
def request_refund(order_id: uuid.UUID, data: RefundIn, db: DB, user: Current):
    refunds.make_request(db, order_id, user.id, data.reason)
    return orders.order_view(db, orders.find_order(db, order_id, user_id=user.id))


def _receive_notification(
    db: Session,
    provider_id: uuid.UUID,
    raw: bytes,
    headers,
    *,
    is_get=False,
):
    provider = db.get(BillingProvider, provider_id)
    if provider is None:
        raise NotFound("支付渠道不存在")
    if is_get and provider.kind != "easypay":
        raise HTTPException(status_code=405, detail="Method Not Allowed", headers={"Allow": "POST"})
    try:
        evidence = orders.provider_for(provider).verify_webhook(raw, headers)
    except ValueError as exc:
        raise CapabilityUnsupported("支付通知验证失败") from exc
    if evidence is not None:
        orders.process_confirmation(db, provider_id, evidence)
    return provider.kind


@router.post("/billing/webhook/{provider_id}", include_in_schema=False, status_code=204)
async def webhook(provider_id: uuid.UUID, request: Request, db: DB):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 1_048_576:
            raise CapabilityUnsupported("支付通知超过大小限制")
        raw.extend(chunk)
    kind = await run_in_threadpool(
        _receive_notification, db, provider_id, bytes(raw), request.headers
    )
    if kind in {"alipay", "easypay"}:
        return PlainTextResponse("success", headers={"Cache-Control": "no-store"})
    return Response(status_code=204)


@router.get("/billing/webhook/{provider_id}", include_in_schema=False)
async def easypay_webhook(provider_id: uuid.UUID, request: Request, db: DB):
    raw = request.scope["query_string"]
    if len(raw) > 16_384:
        raise CapabilityUnsupported("支付通知超过大小限制")
    await run_in_threadpool(
        _receive_notification, db, provider_id, raw, request.headers, is_get=True
    )
    return PlainTextResponse("success", headers={"Cache-Control": "no-store"})


@router.get("/admin/billing/settings")
def admin_settings(db: DB, _: Super):
    return _settings_view(orders.config(db))


@router.patch("/admin/billing/settings")
def change_settings(data: SettingsIn, db: DB, user: Super):
    # Hold the existing configuration while validating opening purchases. An
    # older client cannot race a new rate edit and put its stale read back.
    current = db.scalar(
        select(BillingSettings)
        .where(BillingSettings.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    values = data.model_dump()
    rate_provided = "subscription_usd_to_cny_rate" in data.model_fields_set
    if not rate_provided:
        values["subscription_usd_to_cny_rate"] = (
            current.subscription_usd_to_cny_rate if current else None
        )
    if data.enabled or data.public_origin:
        values["public_origin"] = orders.public_checkout_origin(data.public_origin)
    if data.enabled:
        currencies = set(db.scalars(select(BillingProvider.currency).where(BillingProvider.active)))
        products = [
            *db.scalars(select(BillingPack).where(BillingPack.active)),
            *db.scalars(select(MembershipPlan).where(MembershipPlan.active)),
        ]
        compatible = any(
            orders.settlement_quote(product, currency, values["subscription_usd_to_cny_rate"])
            for product in products
            for currency in currencies
        )
        if not compatible:
            raise CapabilityUnsupported(
                "启用收款前请至少配置一个在售商品和可结算的支付渠道："
                "币种相同，或已配置美元会员的人民币结算汇率"
            )
    updates = {**values, "updated_at": func.now()}
    if not rate_provided:
        # Also protect first-save races where there was no singleton to lock.
        updates.pop("subscription_usd_to_cny_rate")
    row = db.scalar(
        insert(BillingSettings)
        .values(id=1, **values)
        .on_conflict_do_update(index_elements=[BillingSettings.id], set_=updates)
        .returning(BillingSettings)
        .execution_options(populate_existing=True)
    )
    _audit(db, user, "billing.settings_updated")
    return _settings_view(row)


@router.get("/admin/billing/packs")
def list_packs(db: DB, _: Super):
    return [
        _pack_view(pack)
        for pack in db.scalars(select(BillingPack).order_by(BillingPack.created_at.desc()))
    ]


@router.post("/admin/billing/packs")
def create_pack(data: PackIn, db: DB, user: Super):
    row = BillingPack(**data.model_dump())
    db.add(row)
    db.flush()
    _audit(db, user, "billing.pack_created", row.id)
    return _pack_view(row)


@router.patch("/admin/billing/packs/{pack_id}")
def update_pack(pack_id: uuid.UUID, data: PackIn, db: DB, user: Super):
    row = db.get(BillingPack, pack_id)
    if row is None:
        raise NotFound("积分包不存在")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    _audit(db, user, "billing.pack_updated", row.id)
    return _pack_view(row)


@router.get("/admin/billing/plans")
def list_plans(db: DB, _: Super):
    return [
        _plan_view(row)
        for row in db.scalars(
            select(MembershipPlan).order_by(
                MembershipPlan.sort_order, MembershipPlan.created_at, MembershipPlan.id
            )
        )
    ]


@router.post("/admin/billing/plans")
def create_plan(data: PlanIn, db: DB, user: Super):
    row = MembershipPlan(**_plan_values(data, db))
    db.add(row)
    db.flush()
    _audit(db, user, "billing.plan_created", row.id)
    return _plan_view(row)


@router.patch("/admin/billing/plans/{plan_id}")
def update_plan(plan_id: uuid.UUID, data: PlanIn, db: DB, user: Super):
    row = db.get(MembershipPlan, plan_id)
    if row is None:
        raise NotFound("会员套餐不存在")
    for key, value in _plan_values(data, db, row).items():
        setattr(row, key, value)
    _audit(db, user, "billing.plan_updated", row.id)
    return _plan_view(row)


@router.get("/admin/billing/memberships")
def admin_memberships(
    db: DB,
    _: Super,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows = db.execute(
        select(MembershipTerm, BillingOrder, User)
        .join(BillingOrder, MembershipTerm.order_id == BillingOrder.id)
        .join(User, MembershipTerm.user_id == User.id)
        .order_by(BillingOrder.created_at.desc(), BillingOrder.id.desc())
        .limit(limit)
        .offset(offset)
    )
    moment = orders.now()
    return [
        {
            **memberships.term_view(term, order, moment),
            "user_id": owner.id,
            "user_name": owner.display_name,
            "user_email": owner.email,
        }
        for term, order, owner in rows
    ]


@router.get("/admin/billing/provider-types")
def provider_types(_: Super):
    return provider_catalog()


@router.get("/admin/billing/providers")
def list_providers(db: DB, _: Super):
    return [
        _provider_view(row)
        for row in db.scalars(select(BillingProvider).order_by(BillingProvider.created_at.desc()))
    ]


@router.post("/admin/billing/providers")
def create_provider(data: ProviderIn, db: DB, user: Super):
    if data.id is not None and db.get(BillingProvider, data.id) is not None:
        raise Conflict("支付渠道编号已被使用")
    credentials = {key: secret.get_secret_value().strip() for key, secret in data.config.items()}
    if any(len(value) > 20_000 for value in credentials.values()):
        raise CapabilityUnsupported("支付渠道参数过长")
    if data.kind in {"wechatpay", "alipay", "easypay"} and data.currency != "cny":
        raise CapabilityUnsupported("该支付渠道仅支持人民币")
    if (
        data.kind == "stripe"
        and settings.app_env.lower() in {"prod", "production"}
        and not credentials.get("secret_key", "").startswith(("sk_live_", "rk_live_"))
    ):
        raise CapabilityUnsupported("生产环境的 Stripe 渠道需要正式密钥")
    try:
        validate_provider_config(data.kind, credentials)
    except (ValueError, ProviderError) as exc:
        raise CapabilityUnsupported("支付渠道参数缺失或格式有误") from exc
    row = BillingProvider(
        **data.model_dump(exclude={"config"}, exclude_none=True),
        config_enc=orders.encrypt_config(credentials),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", "")
            == "billing_provider_pkey"
        ):
            raise Conflict("支付渠道编号已被使用") from exc
        raise
    _audit(db, user, "billing.provider_created", row.id)
    return _provider_view(row)


@router.patch("/admin/billing/providers/{provider_id}")
def update_provider(provider_id: uuid.UUID, data: ProviderEdit, db: DB, user: Super):
    row = db.get(BillingProvider, provider_id)
    if row is None:
        raise NotFound("支付渠道不存在")
    # Credentials stay attached to this instance for historical orders. A new
    # merchant account gets a separate instance and its own callback URL.
    row.name, row.active, row.allow_refunds = data.name, data.active, data.allow_refunds
    _audit(db, user, "billing.provider_updated", row.id)
    return _provider_view(row)


@router.get("/admin/billing/orders")
def admin_orders(
    db: DB,
    _: Super,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Literal["pending", "paid", "completed", "expired", "failed", "cancelled"] | None = None,
    order_id: uuid.UUID | None = None,
    search: Annotated[str | None, Query(min_length=1, max_length=254)] = None,
    refund_attention: bool = False,
):
    stmt = select(BillingOrder)
    if status is not None:
        stmt = stmt.where(BillingOrder.status == status)
    if order_id is not None:
        stmt = stmt.where(BillingOrder.id == order_id)
    if search and search.strip():
        term = search.strip()
        stmt = stmt.join(User, User.id == BillingOrder.user_id).where(
            or_(
                User.email.icontains(term, autoescape=True),
                User.username.icontains(term, autoescape=True),
                cast(BillingOrder.id, String).icontains(term, autoescape=True),
                cast(BillingOrder.user_id, String).icontains(term, autoescape=True),
            )
        )
    if refund_attention:
        stmt = stmt.where(
            select(BillingRefund.id)
            .where(
                BillingRefund.order_id == BillingOrder.id,
                BillingRefund.status.in_(("requested", "pending", "failed")),
            )
            .exists()
        )
    rows = db.scalars(
        stmt.order_by(BillingOrder.created_at.desc(), BillingOrder.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [orders.order_view(db, row, admin=True) for row in rows]


@router.post("/admin/billing/orders/{order_id}/check")
def admin_check_order(order_id: uuid.UUID, db: DB, _: Super):
    return orders.order_view(db, orders.check_order(db, order_id), admin=True)


@router.post("/admin/billing/orders/{order_id}/refunds")
def create_admin_refund(order_id: uuid.UUID, data: AdminRefundIn, db: DB, user: Super):
    row = refunds.make_request(
        db,
        order_id,
        user.id,
        data.reason,
        amount_minor=data.amount_minor,
        idempotency_key=data.idempotency_key,
        admin=True,
    )
    refunds.approve(db, row.id, user.id)
    return orders.order_view(db, orders.find_order(db, order_id), admin=True)


@router.post("/admin/billing/refunds/{refund_id}/approve")
def approve_refund(refund_id: uuid.UUID, db: DB, user: Super):
    row = refunds.approve(db, refund_id, user.id)
    return orders.order_view(db, orders.find_order(db, row.order_id), admin=True)


@router.post("/admin/billing/refunds/{refund_id}/reject")
def reject_refund(refund_id: uuid.UUID, db: DB, user: Super):
    row = refunds.reject(db, refund_id, user.id)
    return orders.order_view(db, orders.find_order(db, row.order_id), admin=True)


@router.post("/admin/billing/refunds/{refund_id}/check")
def check_refund(refund_id: uuid.UUID, db: DB, _: Super):
    row = refunds.check_refund(db, refund_id)
    return orders.order_view(db, orders.find_order(db, row.order_id), admin=True)


@router.post("/admin/billing/refunds/{refund_id}/resolve")
def resolve_refund(refund_id: uuid.UUID, data: ResolveRefundIn, db: DB, user: Super):
    row = refunds.resolve_easypay_refund(db, refund_id, data.outcome, user.id, data.reason)
    return orders.order_view(db, orders.find_order(db, row.order_id), admin=True)


def _reservation_view(row: CreditReservation, job: GenerationJob) -> dict:
    return {
        "job_id": row.job_id,
        "user_id": row.user_id,
        "units": row.units,
        "job_status": job.status,
        "status": row.status,
        "updated_at": row.updated_at,
    }


@router.get("/admin/billing/reservations")
def unresolved_points(db: DB, _: Super, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    rows = db.execute(
        select(CreditReservation, GenerationJob)
        .join(GenerationJob, GenerationJob.id == CreditReservation.job_id)
        .where(
            CreditReservation.status == "reserved",
            GenerationJob.status.in_(("failed", "canceled")),
        )
        .order_by(CreditReservation.updated_at.desc())
        .limit(limit)
    )
    return [_reservation_view(row, job) for row, job in rows]


@router.post("/admin/billing/reservations/{job_id}/resolve")
def resolve_points(job_id: uuid.UUID, data: ResolveReservationIn, db: DB, user: Super):
    # Retry/resume takes the same original job lock before linking a new
    # attempt to this reservation. An operator cannot race a live attempt.
    job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
    row = db.get(CreditReservation, job_id)
    if not job or not row:
        raise NotFound("任务积分预留不存在")
    if row.status != "reserved":
        return _reservation_view(row, job)
    if job.status not in {"failed", "canceled"}:
        raise Conflict("任务仍在处理，不能人工结算")
    from app.modules.generation.billing import has_active_receiver

    if has_active_receiver(db, job_id):
        raise Conflict("已有任务正在恢复或已完成，请刷新状态")
    if data.action == "release":
        if data.units is not None:
            raise CapabilityUnsupported("退回预留时无需填写扣费积分")
        credit_wallet.release_generation(db, job_id)
    else:
        if data.units is None or data.units > row.units:
            raise CapabilityUnsupported("扣费积分必须在预留额度以内")
        credit_wallet.settle_generation(db, job_id, data.units)
    action = "billing.generation_hold_resolved"
    audit.record(
        db,
        action=action,
        user_id=user.id,
        target_type="generation_job",
        target_id=job_id,
        detail={"resolution": data.action, "reason": data.reason, "units": data.units},
    )
    db.refresh(row)
    return _reservation_view(row, job)
