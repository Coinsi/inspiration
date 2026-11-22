"""Checkout, payment confirmation, and activation of purchased points."""

import base64
import io
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.config import settings
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, ProviderError
from app.models.billing import (
    BillingOrder,
    BillingPack,
    BillingProvider,
    BillingRefund,
    BillingSettings,
)
from app.models.identity import User
from app.modules.billing import wallet
from app.modules.billing.providers import CheckoutRequest, PaymentEvidence, get_provider

logger = logging.getLogger(__name__)
MAX_ACTIVE_ORDERS = 5


def now() -> datetime:
    return datetime.now(UTC)


def config(db: Session) -> BillingSettings:
    return db.get(BillingSettings, 1) or BillingSettings(
        id=1, enabled=False, payment_required=False, public_origin="", checkout_minutes=60
    )


def public_checkout_origin(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        _ = parsed.port
    except ValueError as exc:
        raise CapabilityUnsupported("请输入有效的收款站点地址") from exc
    if (
        not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and is_local
                and settings.app_env.lower() not in {"prod", "production"}
            )
        )
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(c.isspace() or c in "\\%" for c in value)
    ):
        raise CapabilityUnsupported("收款站点必须填写完整的 HTTPS 域名，且不含路径")
    return value


def _fernet() -> Fernet:
    key = settings.credentials_fernet_key
    if not key:
        raise CapabilityUnsupported("请先配置服务器的凭证加密密钥")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise CapabilityUnsupported("服务器的凭证加密密钥无效") from exc


def encrypt_config(values: dict[str, str]) -> bytes:
    return _fernet().encrypt(json.dumps(values, ensure_ascii=False).encode())


def provider_for(provider: BillingProvider):
    try:
        credentials = json.loads(_fernet().decrypt(provider.config_enc))
    except (ValueError, InvalidToken) as exc:
        raise ProviderError("支付渠道凭证不可用") from exc
    return get_provider(provider.kind, credentials)


def find_order(
    db: Session, order_id: uuid.UUID, *, user_id: uuid.UUID | None = None, lock: bool = False
) -> BillingOrder:
    query = select(BillingOrder).where(BillingOrder.id == order_id)
    if user_id is not None:
        query = query.where(BillingOrder.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = db.scalar(query)
    if result is None:
        raise NotFound("支付订单不存在")
    return result


def checkout_request(order: BillingOrder) -> CheckoutRequest:
    redirect = f"{order.public_origin}/billing?order={order.id}"
    return CheckoutRequest(
        order_id=str(order.id),
        amount_minor=order.amount_minor,
        currency=order.currency,
        title=order.pack_name,
        success_url=redirect,
        cancel_url=redirect,
        webhook_url=f"{order.public_origin}/api/v1/billing/webhook/{order.provider_id}",
        expires_at=order.expires_at,
    )


def create_order(
    db: Session,
    user_id: uuid.UUID,
    pack_id: uuid.UUID,
    provider_id: uuid.UUID,
    idempotency_key: uuid.UUID,
) -> BillingOrder:
    # Serialize order creation and the pending-order limit per account.
    db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    existing = db.scalar(
        select(BillingOrder).where(
            BillingOrder.user_id == user_id, BillingOrder.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (existing.pack_id, existing.provider_id) != (pack_id, provider_id):
            raise Conflict("重复请求与原订单内容不一致")
        db.commit()
        return ensure_checkout(db, existing.id)

    preferences = config(db)
    pack = db.get(BillingPack, pack_id)
    provider = db.get(BillingProvider, provider_id)
    if not preferences.enabled:
        raise CapabilityUnsupported("当前未开放积分购买")
    if not pack or not pack.active or not provider or not provider.active:
        raise CapabilityUnsupported("此积分包或支付渠道已下架")
    if pack.currency != provider.currency:
        raise CapabilityUnsupported("此支付渠道不支持积分包的币种")
    provider_for(provider)
    timestamp = now()
    pending = db.scalar(
        select(func.count())
        .select_from(BillingOrder)
        .where(
            BillingOrder.user_id == user_id,
            BillingOrder.status == "pending",
            BillingOrder.expires_at > timestamp,
        )
    )
    if pending >= MAX_ACTIVE_ORDERS:
        raise Conflict("待支付订单过多，请先完成已有订单")
    order = BillingOrder(
        user_id=user_id,
        pack_id=pack.id,
        provider_id=provider.id,
        idempotency_key=idempotency_key,
        pack_name=pack.name,
        provider_name=provider.name,
        currency=pack.currency,
        amount_minor=pack.amount_minor,
        credits=pack.credits,
        public_origin=public_checkout_origin(preferences.public_origin),
        expires_at=timestamp + timedelta(minutes=preferences.checkout_minutes),
        next_check_at=timestamp + timedelta(minutes=1),
    )
    db.add(order)
    db.flush()
    oid = order.id
    # Persist the server-priced order before making a request upstream.
    db.commit()
    return ensure_checkout(db, oid)


def ensure_checkout(db: Session, order_id: uuid.UUID) -> BillingOrder:
    order = find_order(db, order_id)
    if order.status != "pending" or order.provider_order_id or order.expires_at <= now():
        return order
    adapter = provider_for(db.get(BillingProvider, order.provider_id))
    request = checkout_request(order)
    # All adapters use the same upstream key for retries of this order.
    db.commit()
    try:
        result = adapter.create_checkout(request)
    except ProviderError:
        # A timeout may have happened after the provider created the checkout.
        # Keep this order so the same key is reused for a retry.
        logger.warning("Checkout session creation did not confirm order=%s", order_id)
        return find_order(db, order_id)
    order = find_order(db, order_id, lock=True)
    if order.provider_order_id and order.provider_order_id != result.provider_order_id:
        raise Conflict("支付渠道返回了不同的订单编号")
    if order.status == "pending":
        order.provider_order_id = result.provider_order_id
        order.checkout_url = result.checkout_url
        order.qr_payload = result.qr_payload
    db.flush()
    return order


def confirm_payment(db: Session, provider_id: uuid.UUID, evidence: PaymentEvidence) -> BillingOrder:
    try:
        order_id = uuid.UUID(evidence.order_id)
    except (ValueError, TypeError) as exc:
        raise CapabilityUnsupported("支付通知中的订单编号无效") from exc
    order = find_order(db, order_id, lock=True)
    if order.provider_id != provider_id:
        raise Conflict("支付渠道与原订单不一致")
    if order.provider_order_id and evidence.provider_order_id not in (
        None,
        order.provider_order_id,
    ):
        raise Conflict("支付平台订单编号与原订单不一致")
    if evidence.state == "expired" and order.status == "pending":
        order.status = "expired"
        db.flush()
    if evidence.state != "paid":
        return order
    if (
        evidence.amount_minor != order.amount_minor
        or evidence.currency != order.currency
        or not evidence.payment_id
    ):
        raise Conflict("支付金额、币种或交易编号与订单不一致")
    if order.payment_id and order.payment_id != evidence.payment_id:
        raise Conflict("支付交易编号与原记录不一致")
    if order.status != "completed":
        if order.paid_at is None:
            audit.record(
                db,
                action="billing.payment_confirmed",
                user_id=order.user_id,
                target_type="billing_order",
                target_id=order.id,
            )
            order.paid_at = now()
        order.provider_order_id = evidence.provider_order_id or order.provider_order_id
        order.payment_id = evidence.payment_id
        order.status = "paid"
        order.next_check_at = now()
        db.flush()
    return order


def fulfill_order(db: Session, order_id: uuid.UUID) -> BillingOrder:
    order = find_order(db, order_id, lock=True)
    if order.status != "paid":
        return order
    wallet.change(db, order.user_id, "purchase", order.id, available=order.credits)
    order.status = "completed"
    order.completed_at = now()
    audit.record(
        db,
        action="billing.points_credited",
        user_id=order.user_id,
        target_type="billing_order",
        target_id=order.id,
    )
    db.flush()
    return order


def process_confirmation(
    db: Session, provider_id: uuid.UUID, evidence: PaymentEvidence
) -> BillingOrder:
    order = confirm_payment(db, provider_id, evidence)
    if order.status != "paid":
        return order
    order_id = order.id
    # Recovery can find a paid order even if fulfillment fails after receipt.
    db.commit()
    return fulfill_order(db, order_id)


def check_order(db: Session, order_id: uuid.UUID, *, worker: bool = False) -> BillingOrder:
    order = find_order(db, order_id, lock=True)
    if order.status == "paid":
        return fulfill_order(db, order_id)
    if order.status not in {"pending", "expired", "cancelled", "failed"}:
        return order
    if worker and order.next_check_at > now():
        return order
    if not worker and order.next_check_at > now() + timedelta(seconds=45):
        return order
    order.next_check_at = now() + timedelta(
        minutes=60 if worker and order.expires_at < now() else 1
    )
    if order.status == "pending" and not order.provider_order_id and order.expires_at > now():
        db.commit()
        order = ensure_checkout(db, order_id)
        if not order.provider_order_id:
            return order
    provider_id = order.provider_id
    adapter = provider_for(db.get(BillingProvider, provider_id))
    request = checkout_request(order)
    provider_reference = order.provider_order_id
    db.commit()
    evidence = adapter.query_order(request, provider_reference)
    if evidence.order_id != str(order_id):
        raise Conflict("查询返回了不匹配的支付订单")
    return process_confirmation(db, provider_id, evidence)


def cancel_order(db: Session, order_id: uuid.UUID, user_id: uuid.UUID) -> BillingOrder:
    order = find_order(db, order_id, user_id=user_id, lock=True)
    if order.status != "pending":
        return order
    if not order.provider_order_id:
        raise Conflict("正在确认支付渠道状态，请稍后查询订单")
    provider_id = order.provider_id
    adapter = provider_for(db.get(BillingProvider, provider_id))
    request = checkout_request(order)
    reference = order.provider_order_id
    db.commit()
    evidence = adapter.close_order(request, reference)
    if evidence.order_id != str(order_id) or evidence.provider_order_id != reference:
        raise Conflict("支付平台返回了不匹配的订单")
    if evidence.state == "paid":
        return process_confirmation(db, provider_id, evidence)
    order = find_order(db, order_id, user_id=user_id, lock=True)
    if order.status == "pending" and evidence.state == "expired":
        order.status = "cancelled"
        audit.record(
            db,
            action="billing.order_cancelled",
            user_id=user_id,
            target_type="billing_order",
            target_id=order.id,
        )
        db.flush()
    return order


@lru_cache(maxsize=128)
def qr_image(payload: str) -> str | None:
    if not payload or len(payload.encode("utf-8")) > 1500:
        return None
    import qrcode

    png = io.BytesIO()
    qrcode.make(payload, box_size=6, border=4).save(png, format="PNG")
    return "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii")


def refund_view(refund: BillingRefund) -> dict:
    return {
        "id": refund.id,
        "status": refund.status,
        "amount_minor": refund.amount_minor,
        "credits": refund.credits,
        "reason": refund.reason,
        "created_at": refund.created_at,
    }


def order_view(
    db: Session, order: BillingOrder, *, admin: bool = False, include_checkout: bool = True
) -> dict:
    provider = db.get(BillingProvider, order.provider_id)
    refunds = list(
        db.scalars(
            select(BillingRefund)
            .where(BillingRefund.order_id == order.id)
            .order_by(BillingRefund.created_at.desc())
        )
    )
    can_checkout = (
        not admin and include_checkout and (order.status == "pending" and order.expires_at > now())
    )
    result = {
        "id": order.id,
        "status": order.status,
        "pack_name": order.pack_name,
        "provider_name": order.provider_name,
        "currency": order.currency,
        "amount_minor": order.amount_minor,
        "credits": order.credits,
        "checkout_url": order.checkout_url if can_checkout else None,
        "qr_image_url": qr_image(order.qr_payload) if can_checkout and order.qr_payload else None,
        "created_at": order.created_at,
        "expires_at": order.expires_at,
        "paid_at": order.paid_at,
        "refunded_minor": order.refunded_minor,
        "cancel_allowed": order.status == "pending" and bool(order.provider_order_id),
        "refunds": [refund_view(item) for item in refunds],
        "refund_allowed": bool(
            provider
            and provider.allow_refunds
            and order.status == "completed"
            and order.refunded_minor < order.amount_minor
            and not any(item.status in {"requested", "pending"} for item in refunds)
        ),
    }
    if admin:
        owner = db.get(User, order.user_id)
        result.update(user_id=order.user_id, user_email=owner.email if owner else None)
    return result
