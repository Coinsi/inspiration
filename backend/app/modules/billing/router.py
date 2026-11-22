"""User checkout and super-admin payment operations."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
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
    CreditEntry,
    CreditReservation,
)
from app.models.generation import GenerationJob
from app.models.identity import User
from app.modules.billing import orders, refunds
from app.modules.billing import wallet as credit_wallet
from app.modules.billing.providers import provider_catalog, validate_provider_config

router = APIRouter(tags=["billing"])
DB = Annotated[Session, Depends(get_db, scope="function")]
Current = Annotated[User, Depends(get_current_user)]
Super = Annotated[User, Depends(super_admin)]
Currency = Literal["cny", "usd", "eur", "gbp", "hkd", "sgd", "jpy"]
Name = Annotated[str, Field(min_length=1, max_length=100)]
Money = Annotated[int, Field(strict=True, gt=0, le=10**9)]
Credits = Annotated[int, Field(strict=True, gt=0, le=10**10)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SettingsIn(Input):
    enabled: bool
    payment_required: bool
    public_origin: str = Field(max_length=300)
    checkout_minutes: int = Field(strict=True, ge=35, le=120)


class PackIn(Input):
    name: Name
    description: str = Field(default="", max_length=500)
    currency: Currency
    amount_minor: Money
    credits: Credits
    active: bool = True


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
    pack_id: uuid.UUID
    provider_id: uuid.UUID
    idempotency_key: uuid.UUID


class RefundIn(Input):
    reason: Reason


class AdminRefundIn(RefundIn):
    amount_minor: Money
    idempotency_key: uuid.UUID


class ResolveReservationIn(Input):
    action: Literal["release", "settle"]
    reason: Reason
    units: int | None = Field(default=None, strict=True, gt=0, le=10**12)


def _pack_view(pack: BillingPack) -> dict:
    return {
        "id": pack.id,
        "name": pack.name,
        "description": pack.description,
        "currency": pack.currency,
        "amount_minor": pack.amount_minor,
        "credits": pack.credits,
        "active": pack.active,
    }


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
    }


def _audit(db, user, action, ident=None):
    audit.record(db, action=action, user_id=user.id, target_type="billing", target_id=ident)


@router.get("/billing/catalog")
def catalog(db: DB, _: Current):
    enabled = orders.config(db).enabled
    if not enabled:
        return {"enabled": False, "packs": [], "providers": []}
    providers = list(
        db.scalars(
            select(BillingProvider).where(BillingProvider.active).order_by(BillingProvider.name)
        )
    )
    currencies = {provider.currency for provider in providers}
    packs = db.scalars(
        select(BillingPack)
        .where(BillingPack.active, BillingPack.currency.in_(currencies))
        .order_by(BillingPack.currency, BillingPack.amount_minor)
    )
    return {
        "enabled": True,
        "packs": [_pack_view(pack) for pack in packs],
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
        "available": account.available if account else 0,
        "reserved": account.reserved if account else 0,
        "entries": [
            {
                "id": entry.id,
                "kind": entry.kind,
                "available_delta": entry.available_delta,
                "reserved_delta": entry.reserved_delta,
                "created_at": entry.created_at,
            }
            for entry in entries
        ],
    }


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
def create_order(data: CheckoutIn, db: DB, user: Current):
    row = orders.create_order(db, user.id, data.pack_id, data.provider_id, data.idempotency_key)
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


def _receive_notification(db: Session, provider_id: uuid.UUID, raw: bytes, headers):
    provider = db.get(BillingProvider, provider_id)
    if provider is None:
        raise NotFound("支付渠道不存在")
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
    if kind == "alipay":
        return PlainTextResponse("success")
    return Response(status_code=204)


@router.get("/admin/billing/settings")
def admin_settings(db: DB, _: Super):
    return _settings_view(orders.config(db))


@router.patch("/admin/billing/settings")
def change_settings(data: SettingsIn, db: DB, user: Super):
    values = data.model_dump()
    if data.enabled or data.public_origin:
        values["public_origin"] = orders.public_checkout_origin(data.public_origin)
    if data.enabled:
        compatible = db.scalar(
            select(func.count())
            .select_from(BillingPack)
            .join(
                BillingProvider,
                BillingPack.currency == BillingProvider.currency,
            )
            .where(BillingPack.active, BillingProvider.active)
        )
        if not compatible:
            raise CapabilityUnsupported("启用收款前请至少配置一个积分包和同币种渠道")
    db.execute(
        insert(BillingSettings)
        .values(id=1, **values)
        .on_conflict_do_update(
            index_elements=[BillingSettings.id], set_={**values, "updated_at": func.now()}
        )
    )
    _audit(db, user, "billing.settings_updated")
    return values


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
    if data.kind in {"wechatpay", "alipay"} and data.currency != "cny":
        raise CapabilityUnsupported("微信和支付宝渠道目前仅支持人民币")
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
    linked = db.scalar(
        select(GenerationJob.id)
        .where(
            GenerationJob.input_snapshot["billing"]["reservation_job_id"].astext == str(job_id),
            GenerationJob.status.in_(("pending", "submitted", "running", "succeeded")),
        )
        .limit(1)
    )
    if linked:
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
