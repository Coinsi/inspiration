"""Points are stored in hundredths. Ledger writes share the caller's transaction."""

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, QuotaExceeded
from app.models.billing import (
    BillingSettings,
    CreditAccount,
    CreditAdjustment,
    CreditEntry,
    CreditReservation,
    MembershipAllocation,
)
from app.models.identity import User
from app.modules.billing import memberships

UNITS_PER_POINT = 100
MAX_ADJUSTMENT_UNITS = 10**10
MAX_WELCOME_UNITS = 10_000 * UNITS_PER_POINT


def exact_points_to_units(points: str, *, maximum: int, allow_zero: bool = False) -> int:
    """Administrator input never rounds, accepts exponents, or uses floats."""
    if (
        not isinstance(points, str)
        or len(points) > 32
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", points.strip())
    ):
        raise CapabilityUnsupported("积分须为普通十进制字符串，最多保留两位小数")
    units = int(Decimal(points.strip()) * UNITS_PER_POINT)
    if not (0 if allow_zero else 1) <= units <= maximum:
        raise CapabilityUnsupported("积分数量超出允许范围")
    return units


def units_to_points(units: int) -> str:
    return format((Decimal(units) / UNITS_PER_POINT).normalize(), "f")


def balance_view(account: CreditAccount | None) -> dict:
    return {
        "available": account.available if account else 0,
        "reserved": account.reserved if account else 0,
        "grant_available": account.grant_available if account else 0,
        "grant_reserved": account.grant_reserved if account else 0,
    }


def points_to_units(points) -> int:
    try:
        # Existing quotes are calculated to two decimal places. Normalize
        # floating-point estimates before entering the integer ledger.
        value = Decimal(str(points))
        units = (value * UNITS_PER_POINT).quantize(Decimal(1))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CapabilityUnsupported("模型积分报价无效") from exc
    if not units.is_finite() or units < 0 or units > 10**12:
        raise CapabilityUnsupported("模型积分报价无效")
    return int(units)


def payment_required(db: Session) -> bool:
    config = db.get(BillingSettings, 1)
    return bool(config and config.payment_required)


def lock_account(db: Session, user_id: uuid.UUID) -> CreditAccount:
    """Serialize every permanent and membership balance change for this user."""
    db.execute(
        insert(CreditAccount)
        .values(user_id=user_id, available=0, reserved=0)
        .on_conflict_do_nothing(index_elements=[CreditAccount.user_id])
    )
    return db.scalars(
        select(CreditAccount)
        .where(CreditAccount.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def change(
    db: Session,
    user_id: uuid.UUID,
    kind: str,
    reference_id: uuid.UUID,
    *,
    available: int = 0,
    reserved: int = 0,
    grant_available: int = 0,
    grant_reserved: int = 0,
) -> None:
    account = lock_account(db, user_id)
    existing = db.scalar(
        select(CreditEntry).where(
            CreditEntry.kind == kind, CreditEntry.reference_id == reference_id
        )
    )
    if existing:
        if (
            existing.user_id,
            existing.available_delta,
            existing.reserved_delta,
            existing.grant_available_delta,
            existing.grant_reserved_delta,
        ) != (
            user_id,
            available,
            reserved,
            grant_available,
            grant_reserved,
        ):
            raise Conflict("积分流水与原操作不一致")
        return
    if account.available + available < 0:
        raise QuotaExceeded("可用积分不足")
    if account.reserved + reserved < 0:
        raise Conflict("冻结积分与原操作不一致")
    new_grant_available = account.grant_available + grant_available
    new_grant_reserved = account.grant_reserved + grant_reserved
    if new_grant_available < 0 or new_grant_reserved < 0:
        raise Conflict("赠送积分与原操作不一致")
    # Refunds pass no grant deltas. They can freeze only purchased/legacy
    # credit, never exchange a user's remaining gifts for a cash refund.
    if new_grant_available > account.available + available:
        raise QuotaExceeded("可用的非赠送积分不足，不能占用赠送积分")
    if new_grant_reserved > account.reserved + reserved:
        raise Conflict("冻结积分来源与原操作不一致")
    account.available += available
    account.reserved += reserved
    account.grant_available = new_grant_available
    account.grant_reserved = new_grant_reserved
    db.add(
        CreditEntry(
            user_id=user_id,
            kind=kind,
            reference_id=reference_id,
            available_delta=available,
            reserved_delta=reserved,
            grant_available_delta=grant_available,
            grant_reserved_delta=grant_reserved,
        )
    )
    db.flush()


def adjust(
    db: Session,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    operation: str,
    units: int,
    reason: str,
) -> CreditAdjustment:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在")
    account = lock_account(db, user_id)
    existing = db.scalar(
        select(CreditAdjustment).where(
            CreditAdjustment.user_id == user_id,
            CreditAdjustment.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if (existing.actor_id, existing.operation, existing.units, existing.reason) != (
            actor_id,
            operation,
            units,
            reason,
        ):
            raise Conflict("重复请求与原积分调整内容不一致")
        return existing
    if operation not in {"grant", "revoke"} or not 0 < units <= MAX_ADJUSTMENT_UNITS:
        raise CapabilityUnsupported("积分调整无效")
    if not reason.strip() or len(reason) > 500:
        raise CapabilityUnsupported("请填写积分调整原因")
    if operation == "grant" and not user.is_active:
        raise Conflict("已停用用户不能接收赠送积分")
    if operation == "revoke" and units > account.grant_available:
        raise Conflict("只能收回尚未使用且未被任务预留的赠送积分")
    with db.begin_nested():
        adjustment = CreditAdjustment(
            user_id=user_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            operation=operation,
            units=units,
            reason=reason,
        )
        db.add(adjustment)
        db.flush()
        delta = units if operation == "grant" else -units
        change(
            db,
            user_id,
            "admin_" + operation,
            adjustment.id,
            available=delta,
            grant_available=delta,
        )
        audit.record(
            db,
            action="billing.credit_" + operation,
            user_id=actor_id,
            target_type="credit_account",
            target_id=user_id,
            detail={"adjustment_id": str(adjustment.id), "units": units, "reason": reason},
        )
        db.flush()
    return adjustment


def grant_welcome(db: Session, user_id: uuid.UUID) -> None:
    """Call only while creating a new user, in that same transaction.

    Login/linking never calls this hook. Even a disabled (zero) policy is marked
    processed, so retrying onboarding after a policy change cannot issue a gift.
    """
    account = lock_account(db, user_id)
    if account.welcome_processed_at is not None:
        return
    preferences = db.get(BillingSettings, 1)
    units = preferences.welcome_credits if preferences else 0
    with db.begin_nested():
        if units:
            change(db, user_id, "welcome_grant", user_id, available=units, grant_available=units)
            audit.record(
                db,
                action="billing.welcome_granted",
                user_id=user_id,
                target_type="credit_account",
                target_id=user_id,
                detail={"units": units},
            )
        # change() refreshes the locked account in sessions with autoflush=False.
        # Set the one-time marker after that refresh, including zero policies.
        account.welcome_processed_at = datetime.now(UTC)
        db.flush()


def _reservation(db: Session, job_id: uuid.UUID) -> CreditReservation | None:
    # The owner never changes. Read it before taking any row lock so every
    # wallet operation locks account -> reservation -> membership terms.
    user_id = db.scalar(select(CreditReservation.user_id).where(CreditReservation.job_id == job_id))
    if user_id is None:
        return None
    lock_account(db, user_id)
    return db.scalar(
        select(CreditReservation)
        .where(CreditReservation.job_id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def reserve_generation(db: Session, user_id: uuid.UUID, job_id: uuid.UUID, units: int) -> None:
    if units <= 0:
        return
    # The caller owns the new job row. Lock the account before looking up the
    # reservation so repeated calls cannot race an initially missing row.
    account = lock_account(db, user_id)
    existing = db.get(CreditReservation, job_id, populate_existing=True)
    if existing:
        if (existing.user_id, existing.units) != (user_id, units):
            raise Conflict("任务积分预留与原记录不一致")
        if existing.status == "released":
            raise Conflict("这笔任务预留已退回，请新建任务")
        return
    # A caller may catch an insufficient-funds error while recording a failed
    # job. Roll back both sources together even when its outer transaction lives.
    with db.begin_nested():
        reserved_terms = memberships.reserve(db, user_id, units, datetime.now(UTC))
        permanent = units - sum(taken for _, taken in reserved_terms)
        if permanent > account.available:
            available = units - permanent + account.available
            raise QuotaExceeded(
                "可用积分不足",
                detail={
                    "reason": "insufficient_personal_credits",
                    "required_units": units,
                    "available_units": available,
                    "shortfall_units": units - available,
                },
            )
        grant_units = min(permanent, account.grant_available)
        if permanent:
            change(
                db,
                user_id,
                "generation_reserve",
                job_id,
                available=-permanent,
                reserved=permanent,
                grant_available=-grant_units,
                grant_reserved=grant_units,
            )
        db.add(
            CreditReservation(job_id=job_id, user_id=user_id, units=units, grant_units=grant_units)
        )
        db.flush()
        for term, taken in reserved_terms:
            db.add(MembershipAllocation(job_id=job_id, order_id=term.order_id, units=taken))
        db.flush()


def _membership_units(db: Session, reservation: CreditReservation) -> int:
    allocated = memberships.allocations(db, reservation.job_id)
    if any(term.user_id != reservation.user_id for term, _ in allocated):
        raise Conflict("任务会员额度与原记录不一致")
    units = sum(allocation.units for _, allocation in allocated)
    if not 0 <= units <= reservation.units:
        raise Conflict("任务会员额度与原记录不一致")
    if not 0 <= reservation.grant_units <= reservation.units - units:
        raise Conflict("任务赠送积分与原记录不一致")
    return units


def settle_generation(db: Session, job_id: uuid.UUID, units: int) -> None:
    reservation = _reservation(db, job_id)
    if reservation is None:
        return
    if units < 0:
        raise Conflict("任务结算积分无效")
    if reservation.status == "settled":
        if reservation.settled_units != units:
            raise Conflict("任务已按不同积分数结算")
        return
    if reservation.status != "reserved":
        raise Conflict("任务积分预留已退回，无法结算")
    with db.begin_nested():
        membership_reserved = _membership_units(db, reservation)
        membership_used = min(units, membership_reserved)
        permanent_reserved = reservation.units - membership_reserved
        permanent_used = units - membership_used
        account = lock_account(db, reservation.user_id)
        # Use the original grant reservation first. Extra actual usage may use
        # currently available gifts, but never another task's reserved gifts.
        grant_used = min(permanent_used, reservation.grant_units) + min(
            max(0, permanent_used - permanent_reserved), account.grant_available
        )
        if permanent_reserved or permanent_used:
            change(
                db,
                reservation.user_id,
                "generation_settle",
                job_id,
                available=permanent_reserved - permanent_used,
                reserved=-permanent_reserved,
                grant_available=reservation.grant_units - grant_used,
                grant_reserved=-reservation.grant_units,
            )
        memberships.settle(db, job_id, membership_used)
        reservation.settled_units = units
        reservation.status = "settled"
        db.flush()


def release_generation(db: Session, job_id: uuid.UUID) -> None:
    reservation = _reservation(db, job_id)
    if reservation is None or reservation.status != "reserved":
        return
    with db.begin_nested():
        permanent_reserved = reservation.units - _membership_units(db, reservation)
        if permanent_reserved:
            change(
                db,
                reservation.user_id,
                "generation_release",
                job_id,
                available=permanent_reserved,
                reserved=-permanent_reserved,
                grant_available=reservation.grant_units,
                grant_reserved=-reservation.grant_units,
            )
        memberships.release(db, job_id)
        reservation.status = "released"
        db.flush()


def has_generation_reservation(db: Session, job_id: uuid.UUID) -> bool:
    return generation_reservation_status(db, job_id) is not None


def generation_reservation_status(db: Session, job_id: uuid.UUID) -> str | None:
    reservation = db.get(CreditReservation, job_id)
    return reservation.status if reservation else None
