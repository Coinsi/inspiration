"""Payment orders and the personal points ledger."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class BillingSettings(Base, TimestampMixin):
    __tablename__ = "billing_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_required: Mapped[bool] = mapped_column(Boolean, default=False)
    public_origin: Mapped[str] = mapped_column(String(300), default="")
    checkout_minutes: Mapped[int] = mapped_column(Integer, default=60)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_billing_settings_singleton"),
        CheckConstraint("checkout_minutes between 35 and 120", name="ck_billing_checkout_minutes"),
    )


class BillingPack(Base, TimestampMixin):
    __tablename__ = "billing_pack"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    credits: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint("amount_minor > 0 and credits > 0", name="ck_billing_pack_amounts"),
    )


class BillingProvider(Base, TimestampMixin):
    __tablename__ = "billing_provider"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    config_enc: Mapped[bytes] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_refunds: Mapped[bool] = mapped_column(Boolean, default=True)


class MembershipPlan(Base, TimestampMixin):
    __tablename__ = "membership_plan"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    features: Mapped[list[str]] = mapped_column(JSONB, default=list)
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    original_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    credits: Mapped[int] = mapped_column(BigInteger)
    duration: Mapped[int] = mapped_column(Integer)
    duration_unit: Mapped[str] = mapped_column(String(8))
    monthly_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership_plan.id"), index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint(
            "amount_minor > 0 and credits > 0 "
            "and (original_amount_minor is null or original_amount_minor >= amount_minor)",
            name="ck_membership_plan_amounts",
        ),
        CheckConstraint(
            "duration > 0 and duration_unit in ('day','week','month') and "
            "duration * case duration_unit when 'week' then 7 when 'month' then 30 "
            "else 1 end <= 3650",
            name="ck_membership_plan_duration",
        ),
        CheckConstraint("jsonb_typeof(features) = 'array'", name="ck_membership_plan_features"),
        CheckConstraint(
            "monthly_plan_id is null or monthly_plan_id <> id",
            name="ck_membership_plan_monthly_link",
        ),
        Index("ix_membership_plan_catalog", "active", "sort_order"),
    )


class BillingOrder(Base, TimestampMixin):
    __tablename__ = "billing_order"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    pack_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("billing_pack.id"))
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("membership_plan.id"))
    membership_days: Mapped[int | None] = mapped_column(Integer)
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_provider.id"))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    pack_name: Mapped[str] = mapped_column(String(100))
    provider_name: Mapped[str] = mapped_column(String(100))
    public_origin: Mapped[str] = mapped_column(String(300))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    credits: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    provider_order_id: Mapped[str | None] = mapped_column(String(200))
    checkout_ip: Mapped[str | None] = mapped_column(String(45))
    checkout_mobile: Mapped[bool | None] = mapped_column(Boolean)
    payment_id: Mapped[str | None] = mapped_column(String(200))
    checkout_url: Mapped[str | None] = mapped_column(Text)
    qr_payload: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_credits: Mapped[int] = mapped_column(BigInteger, default=0)

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_billing_order_idempotency"),
        UniqueConstraint("provider_id", "provider_order_id", name="uq_billing_provider_order"),
        UniqueConstraint("provider_id", "payment_id", name="uq_billing_provider_payment"),
        CheckConstraint(
            "amount_minor > 0 and credits > 0 and refunded_minor between 0 and amount_minor "
            "and refunded_credits between 0 and credits",
            name="ck_billing_order_amounts",
        ),
        CheckConstraint(
            "status in ('pending','paid','completed','failed','expired','cancelled')",
            name="ck_billing_order_status",
        ),
        CheckConstraint(
            "(pack_id is not null and plan_id is null and membership_days is null) or "
            "(pack_id is null and plan_id is not null and membership_days is not null "
            "and membership_days between 1 and 3650)",
            name="ck_billing_order_product",
        ),
        Index("ix_billing_order_reconcile", "status", "next_check_at"),
        Index("ix_billing_order_plan_id", "plan_id"),
    )


class BillingRefund(Base, TimestampMixin):
    __tablename__ = "billing_refund"

    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_order.id"), index=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    credits: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24), default="requested")
    provider_refund_id: Mapped[str | None] = mapped_column(String(200))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("order_id", "idempotency_key", name="uq_billing_refund_idempotency"),
        CheckConstraint("amount_minor > 0 and credits >= 0", name="ck_billing_refund_amounts"),
        CheckConstraint(
            "status in ('requested','pending','succeeded','failed','rejected')",
            name="ck_billing_refund_status",
        ),
        Index("ix_billing_refund_reconcile", "status", "next_check_at"),
    )


class CreditAccount(Base, TimestampMixin):
    __tablename__ = "credit_account"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), primary_key=True)
    available: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)

    __table_args__ = (
        CheckConstraint("available >= 0 and reserved >= 0", name="ck_credit_account_balance"),
    )


class CreditEntry(Base, TimestampMixin):
    __tablename__ = "credit_entry"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credit_account.user_id"))
    kind: Mapped[str] = mapped_column(String(32))
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    available_delta: Mapped[int] = mapped_column(BigInteger)
    reserved_delta: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (
        UniqueConstraint("kind", "reference_id", name="uq_credit_entry_operation"),
        Index("ix_credit_entry_user_created", "user_id", "created_at"),
    )


class CreditReservation(Base, TimestampMixin):
    __tablename__ = "credit_reservation"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_job.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credit_account.user_id"))
    units: Mapped[int] = mapped_column(BigInteger)
    settled_units: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="reserved")

    __table_args__ = (
        CheckConstraint("units > 0 and settled_units >= 0", name="ck_credit_reservation_units"),
        CheckConstraint(
            "status in ('reserved','settled','released')", name="ck_credit_reservation_status"
        ),
    )


class MembershipTerm(Base, TimestampMixin):
    __tablename__ = "membership_term"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_order.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("membership_plan.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credits: Mapped[int] = mapped_column(BigInteger)
    available: Mapped[int] = mapped_column(BigInteger)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(24), default="granted")

    __table_args__ = (
        CheckConstraint("expires_at > starts_at", name="ck_membership_term_period"),
        CheckConstraint(
            "credits > 0 and available >= 0 and reserved >= 0 and available + reserved <= credits",
            name="ck_membership_term_balances",
        ),
        CheckConstraint(
            "status in ('granted','refund_pending','refunded')", name="ck_membership_term_status"
        ),
        Index("ix_membership_term_user_plan", "user_id", "plan_id", "expires_at"),
        Index("ix_membership_term_user_expiry", "user_id", "expires_at"),
    )


class MembershipAllocation(Base, TimestampMixin):
    __tablename__ = "membership_allocation"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_job.id"), primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership_term.order_id"), primary_key=True
    )
    units: Mapped[int] = mapped_column(BigInteger)
    settled_units: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint(
            "units > 0 and (settled_units is null or settled_units between 0 and units)",
            name="ck_membership_allocation_units",
        ),
        Index("ix_membership_allocation_order", "order_id"),
    )
