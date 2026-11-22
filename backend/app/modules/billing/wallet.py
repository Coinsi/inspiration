"""Points are stored in hundredths. Ledger writes share the caller's transaction."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import CapabilityUnsupported, Conflict, QuotaExceeded
from app.models.billing import (
    BillingSettings,
    CreditAccount,
    CreditEntry,
    CreditReservation,
    MembershipAllocation,
)
from app.modules.billing import memberships

UNITS_PER_POINT = 100


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
) -> None:
    account = lock_account(db, user_id)
    existing = db.scalar(
        select(CreditEntry).where(
            CreditEntry.kind == kind, CreditEntry.reference_id == reference_id
        )
    )
    if existing:
        if (existing.user_id, existing.available_delta, existing.reserved_delta) != (
            user_id,
            available,
            reserved,
        ):
            raise Conflict("积分流水与原操作不一致")
        return
    if account.available + available < 0:
        raise QuotaExceeded("可用积分不足")
    if account.reserved + reserved < 0:
        raise Conflict("冻结积分与原操作不一致")
    account.available += available
    account.reserved += reserved
    db.add(
        CreditEntry(
            user_id=user_id,
            kind=kind,
            reference_id=reference_id,
            available_delta=available,
            reserved_delta=reserved,
        )
    )
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
    lock_account(db, user_id)
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
        if permanent:
            change(
                db, user_id, "generation_reserve", job_id, available=-permanent, reserved=permanent
            )
        db.add(CreditReservation(job_id=job_id, user_id=user_id, units=units))
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
        if permanent_reserved or permanent_used:
            change(
                db,
                reservation.user_id,
                "generation_settle",
                job_id,
                available=permanent_reserved - permanent_used,
                reserved=-permanent_reserved,
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
            )
        memberships.release(db, job_id)
        reservation.status = "released"
        db.flush()


def has_generation_reservation(db: Session, job_id: uuid.UUID) -> bool:
    return generation_reservation_status(db, job_id) is not None


def generation_reservation_status(db: Session, job_id: uuid.UUID) -> str | None:
    reservation = db.get(CreditReservation, job_id)
    return reservation.status if reservation else None
