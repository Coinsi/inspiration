"""Refund lifecycle. Money is returned by the original payment provider."""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, ProviderError
from app.models.billing import BillingOrder, BillingProvider, BillingRefund
from app.modules.billing import orders, wallet
from app.modules.billing.providers import RefundEvidence

logger = logging.getLogger(__name__)


def _pair(db: Session, refund_id: uuid.UUID) -> tuple[BillingOrder, BillingRefund]:
    order_id = db.scalar(select(BillingRefund.order_id).where(BillingRefund.id == refund_id))
    if order_id is None:
        raise NotFound("退款记录不存在")
    order = orders.find_order(db, order_id, lock=True)
    refund = db.scalars(
        select(BillingRefund)
        .where(BillingRefund.id == refund_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    return order, refund


def _audit(db: Session, action: str, order: BillingOrder, refund: BillingRefund, actor=None):
    audit.record(
        db,
        action=action,
        user_id=actor or order.user_id,
        target_type="billing_order",
        target_id=order.id,
        detail={"refund_id": str(refund.id), "amount_minor": refund.amount_minor},
    )


def make_request(
    db: Session,
    order_id: uuid.UUID,
    actor: uuid.UUID,
    reason: str,
    *,
    amount_minor: int | None = None,
    idempotency_key: uuid.UUID | None = None,
    admin: bool = False,
) -> BillingRefund:
    order = orders.find_order(db, order_id, user_id=None if admin else actor, lock=True)
    if idempotency_key is not None:
        existing = db.scalar(
            select(BillingRefund).where(
                BillingRefund.order_id == order_id,
                BillingRefund.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if (existing.amount_minor, existing.reason) != (amount_minor, reason):
                raise Conflict("重复请求与原退款内容不一致")
            return existing
    active = db.scalar(
        select(BillingRefund)
        .where(
            BillingRefund.order_id == order_id,
            BillingRefund.status.in_(("requested", "pending")),
        )
        .limit(1)
    )
    if active is not None:
        if not admin:
            return active
        raise Conflict("这笔订单已有待处理的退款")
    provider = db.get(BillingProvider, order.provider_id)
    remaining = order.amount_minor - order.refunded_minor
    if order.status != "completed" or not order.payment_id or remaining <= 0:
        raise CapabilityUnsupported("这笔订单当前无法退款")
    if not provider.allow_refunds:
        raise CapabilityUnsupported("该渠道已关闭退款")
    requested_amount = remaining if amount_minor is None else amount_minor
    if requested_amount <= 0 or requested_amount > remaining:
        raise CapabilityUnsupported("退款金额超出订单可退金额")
    # Calculate from the cumulative refund to avoid losing or creating
    # credits across multiple partial refunds.
    credits_after = order.credits * (order.refunded_minor + requested_amount) // order.amount_minor
    refund = BillingRefund(
        order_id=order.id,
        requested_by=actor,
        idempotency_key=idempotency_key or uuid.uuid4(),
        amount_minor=requested_amount,
        credits=credits_after - order.refunded_credits,
        reason=reason,
        next_check_at=orders.now(),
    )
    db.add(refund)
    db.flush()
    _audit(db, "billing.refund_requested", order, refund, actor)
    return refund


def reject(db: Session, refund_id: uuid.UUID, actor: uuid.UUID) -> BillingRefund:
    order, refund = _pair(db, refund_id)
    if refund.status == "rejected":
        return refund
    if refund.status != "requested":
        raise Conflict("当前退款不能拒绝")
    refund.status = "rejected"
    _audit(db, "billing.refund_rejected", order, refund, actor)
    db.flush()
    return refund


def approve(db: Session, refund_id: uuid.UUID, actor: uuid.UUID) -> BillingRefund:
    order, refund = _pair(db, refund_id)
    if refund.status != "requested":
        return refund
    provider = db.get(BillingProvider, order.provider_id)
    if not provider.allow_refunds or not order.payment_id:
        raise CapabilityUnsupported("该支付渠道当前不可退款")
    orders.provider_for(provider)
    wallet.change(
        db,
        order.user_id,
        "refund_reserve",
        refund.id,
        available=-refund.credits,
        reserved=refund.credits,
    )
    refund.status = "pending"
    refund.submitted_at = orders.now()
    refund.next_check_at = refund.submitted_at
    _audit(db, "billing.refund_approved", order, refund, actor)
    # Preserve the frozen points and the provider refund key before issuing
    # an irreversible network request.
    db.commit()
    return check_refund(db, refund_id, start=True)


def apply_result(db: Session, refund_id: uuid.UUID, result: RefundEvidence) -> BillingRefund:
    order, refund = _pair(db, refund_id)
    if refund.status != "pending":
        return refund
    if (
        (result.amount_minor is not None and result.amount_minor != refund.amount_minor)
        or (result.currency is not None and result.currency != order.currency)
        or (
            refund.provider_refund_id
            and result.provider_refund_id
            not in (
                None,
                refund.provider_refund_id,
            )
        )
    ):
        raise Conflict("支付平台退款金额、币种或编号与原记录不一致")
    if result.provider_refund_id:
        refund.provider_refund_id = result.provider_refund_id
    if result.state == "pending":
        db.flush()
        return refund
    if result.state == "succeeded":
        if result.amount_minor is None or result.currency is None or not refund.provider_refund_id:
            raise Conflict("支付平台退款凭证不完整")
        wallet.change(db, order.user_id, "refund_settle", refund.id, reserved=-refund.credits)
        order.refunded_minor += refund.amount_minor
        order.refunded_credits += refund.credits
    elif result.state == "failed":
        wallet.change(
            db,
            order.user_id,
            "refund_release",
            refund.id,
            available=refund.credits,
            reserved=-refund.credits,
        )
    else:
        raise ProviderError("支付平台返回了未知退款状态")
    refund.status = result.state
    _audit(db, f"billing.refund_{result.state}", order, refund)
    db.flush()
    return refund


def check_refund(
    db: Session, refund_id: uuid.UUID, *, start: bool = False, worker: bool = False
) -> BillingRefund:
    order, refund = _pair(db, refund_id)
    if refund.status != "pending":
        return refund
    moment = orders.now()
    if not start and (
        (worker and refund.next_check_at > moment)
        or (not worker and refund.next_check_at > moment + timedelta(seconds=45))
    ):
        return refund
    adapter = orders.provider_for(db.get(BillingProvider, order.provider_id))
    request = orders.checkout_request(order)
    payment_id = order.payment_id
    provider_refund_id = refund.provider_refund_id
    amount_minor = refund.amount_minor
    # Some providers expire their idempotency keys. Outside the retry window,
    # preserve the hold and use upstream lookup/manual confirmation only.
    may_create = refund.submitted_at and moment - refund.submitted_at < timedelta(hours=12)
    refund.next_check_at = moment + timedelta(minutes=1)
    db.commit()
    try:
        if start:
            evidence = adapter.create_refund(request, payment_id, str(refund_id), amount_minor)
        else:
            try:
                # A successful full refund can close the original trade. Look
                # for the refund itself before attempting to recreate it.
                evidence = adapter.query_refund(
                    request, payment_id, str(refund_id), provider_refund_id
                )
            except ProviderError:
                if provider_refund_id or not may_create:
                    raise
                evidence = adapter.create_refund(request, payment_id, str(refund_id), amount_minor)
    except ProviderError:
        # Network uncertainty is neither success nor a reason to restore
        # points. The saved refund key is reused by later reconciliation.
        logger.warning("Upstream refund could not be confirmed refund=%s", refund_id)
        return _pair(db, refund_id)[1]
    return apply_result(db, refund_id, evidence)
