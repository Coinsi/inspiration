"""Fixed-period membership allowances, independent of permanent wallet points.

Every mutation requires the caller to hold the user's wallet account row lock
BEFORE entering this module. This serializes renewal, refunds and generation
across different orders/jobs. Helpers flush but never commit; callers must keep
the wallet and membership changes in the same transaction (or savepoint).

Each paid order grants one immutable [starts_at, expires_at) period. One month
means 30 days; multi-month purchases receive their entire allowance once. A
refund never reschedules later purchases. Unused generation holds return only
to their original period, even after expiry, and cannot revive expired credit.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import CapabilityUnsupported, Conflict
from app.models.billing import BillingOrder, MembershipAllocation, MembershipTerm

MAX_MEMBERSHIP_DAYS = 3650


def duration_days(duration: int, duration_unit: str) -> int:
    multiplier = (
        {"day": 1, "week": 7, "month": 30}.get(duration_unit)
        if isinstance(duration_unit, str)
        else None
    )
    if type(duration) is not int or duration <= 0 or multiplier is None:
        raise CapabilityUnsupported("会员有效期格式无效")
    days = duration * multiplier
    if days > MAX_MEMBERSHIP_DAYS:
        raise CapabilityUnsupported("会员单期有效期不能超过3650天")
    return days


def _moment(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise Conflict("会员有效期需要明确的时区")
    return value.astimezone(UTC)


def _units(value: int) -> int:
    if type(value) is not int or value < 0:
        raise Conflict("会员额度无效")
    return value


def _term(db: Session, order: BillingOrder) -> MembershipTerm | None:
    term = db.scalar(
        select(MembershipTerm)
        .where(MembershipTerm.order_id == order.id)
        .execution_options(populate_existing=True)
    )
    if term is not None and (
        term.user_id != order.user_id
        or term.plan_id != order.plan_id
        or term.credits != order.credits
    ):
        raise Conflict("会员权益与原订单不一致")
    return term


def fulfill(db: Session, order: BillingOrder, moment: datetime) -> MembershipTerm:
    """Grant the order snapshot once; caller already owns the wallet account lock."""
    moment = _moment(moment)
    if (
        order.plan_id is None
        or order.pack_id is not None
        or type(order.membership_days) is not int
        or not 0 < order.membership_days <= MAX_MEMBERSHIP_DAYS
        or _units(order.credits) == 0
    ):
        raise Conflict("会员订单快照无效")
    existing = _term(db, order)
    if existing is not None:
        return existing
    previous_expiry = db.scalar(
        select(func.max(MembershipTerm.expires_at)).where(
            MembershipTerm.user_id == order.user_id,
            MembershipTerm.plan_id == order.plan_id,
            MembershipTerm.status.in_(("granted", "refund_pending")),
        )
    )
    starts_at = max(moment, _moment(previous_expiry)) if previous_expiry else moment
    try:
        expires_at = starts_at + timedelta(days=order.membership_days)
    except OverflowError:
        raise Conflict("会员累计有效期超出可处理范围，请联系管理员") from None
    term = MembershipTerm(
        order_id=order.id,
        user_id=order.user_id,
        plan_id=order.plan_id,
        starts_at=starts_at,
        expires_at=expires_at,
        credits=order.credits,
        available=order.credits,
        reserved=0,
        status="granted",
    )
    db.add(term)
    db.flush()
    return term


def term_view(term: MembershipTerm, order: BillingOrder, moment: datetime) -> dict:
    moment = _moment(moment)
    if term.status == "granted":
        if moment < _moment(term.starts_at):
            status = "scheduled"
        elif moment >= _moment(term.expires_at):
            status = "expired"
        else:
            status = "active"
    else:
        status = term.status
    return {
        "order_id": term.order_id,
        "plan_id": term.plan_id,
        "plan_name": order.pack_name,
        "starts_at": term.starts_at,
        "expires_at": term.expires_at,
        "status": status,
        "credits": term.credits,
        "available": term.available if status == "active" else 0,
        "reserved": term.reserved,
    }


def for_user(
    db: Session,
    user_id: uuid.UUID,
    moment: datetime,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
        raise CapabilityUnsupported("会员列表分页参数无效")
    rows = db.execute(
        select(MembershipTerm, BillingOrder)
        .join(BillingOrder, BillingOrder.id == MembershipTerm.order_id)
        .where(MembershipTerm.user_id == user_id, BillingOrder.user_id == user_id)
        .order_by(
            MembershipTerm.starts_at.desc(),
            MembershipTerm.expires_at.desc(),
            MembershipTerm.order_id,
        )
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    return [term_view(term, order, moment) for term, order in rows]


def can_refund(db: Session, order: BillingOrder) -> bool:
    """Read-only eligibility; approval must check again while holding the account lock."""
    if (
        order.plan_id is None
        or order.status != "completed"
        or order.refunded_minor != 0
        or order.refunded_credits != 0
    ):
        return False
    term = _term(db, order)
    return bool(
        term is not None
        and term.status == "granted"
        and term.reserved == 0
        and term.available == term.credits
    )


def hold_refund(db: Session, order: BillingOrder) -> MembershipTerm:
    """Freeze an entirely unused period; do not touch its immutable dates."""
    term = _term(db, order)
    if term is not None and term.status == "refund_pending":
        return term
    if not can_refund(db, order):
        raise Conflict("会员仅支持整期未使用且无预留额度时全额退款")
    term.status = "refund_pending"
    db.flush()
    return term


def finalize_refund(
    db: Session, order: BillingOrder, outcome: Literal["succeeded", "failed"]
) -> MembershipTerm:
    term = _term(db, order)
    if term is None or outcome not in ("succeeded", "failed"):
        raise Conflict("会员退款记录无效")
    target = "refunded" if outcome == "succeeded" else "granted"
    if term.status == target:
        return term
    if term.status != "refund_pending" or term.reserved != 0 or term.available != term.credits:
        raise Conflict("会员退款冻结状态与原记录不一致")
    if outcome == "succeeded":
        term.available = 0
    term.status = target
    db.flush()
    return term


def reserve(
    db: Session, user_id: uuid.UUID, units: int, moment: datetime
) -> list[tuple[MembershipTerm, int]]:
    """Reserve up to units; the caller funds the remainder from permanent credit.

    The caller records MembershipAllocation rows for the returned parts in this
    same transaction. A future term never lends credit to the current period.
    """
    remaining = _units(units)
    moment = _moment(moment)
    if remaining == 0:
        return []
    terms = db.scalars(
        select(MembershipTerm)
        .where(
            MembershipTerm.user_id == user_id,
            MembershipTerm.status == "granted",
            MembershipTerm.starts_at <= moment,
            MembershipTerm.expires_at > moment,
            MembershipTerm.available > 0,
        )
        .order_by(MembershipTerm.expires_at, MembershipTerm.starts_at, MembershipTerm.order_id)
        .execution_options(populate_existing=True)
    )
    parts = []
    for term in terms:
        amount = min(remaining, term.available)
        term.available -= amount
        term.reserved += amount
        parts.append((term, amount))
        remaining -= amount
        if remaining == 0:
            break
    db.flush()
    return parts


def allocations(
    db: Session, job_id: uuid.UUID
) -> list[tuple[MembershipTerm, MembershipAllocation]]:
    return list(
        db.execute(
            select(MembershipTerm, MembershipAllocation)
            .join(MembershipAllocation, MembershipAllocation.order_id == MembershipTerm.order_id)
            .where(MembershipAllocation.job_id == job_id)
            .order_by(MembershipTerm.expires_at, MembershipTerm.starts_at, MembershipTerm.order_id)
            .execution_options(populate_existing=True)
        ).tuples()
    )


def settle(db: Session, job_id: uuid.UUID, used_units: int) -> None:
    """Consume membership usage once, returning surplus only to its original term."""
    remaining = _units(used_units)
    rows = allocations(db, job_id)
    if remaining > sum(allocation.units for _, allocation in rows):
        raise Conflict("会员结算额度超过原预留")
    completed = [allocation.settled_units is not None for _, allocation in rows]
    if any(completed):
        if (
            not all(completed)
            or sum(allocation.settled_units for _, allocation in rows) != remaining
        ):
            raise Conflict("会员预留已按不同额度结算")
        return
    # Check every source before mutating any one of them. Refunds cannot freeze
    # a reserved term; expiry, however, is allowed while a task is in flight.
    if any(
        term.status != "granted" or term.reserved < allocation.units for term, allocation in rows
    ):
        raise Conflict("会员预留额度与原记录不一致")
    for term, allocation in rows:
        spent = min(remaining, allocation.units)
        term.reserved -= allocation.units
        term.available += allocation.units - spent
        allocation.settled_units = spent
        remaining -= spent
    db.flush()


def release(db: Session, job_id: uuid.UUID) -> None:
    settle(db, job_id, 0)
