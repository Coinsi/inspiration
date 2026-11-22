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
from sqlalchemy.dialects.postgresql import UUID
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


class BillingOrder(Base, TimestampMixin):
    __tablename__ = "billing_order"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    pack_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_pack.id"))
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
        Index("ix_billing_order_reconcile", "status", "next_check_at"),
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
