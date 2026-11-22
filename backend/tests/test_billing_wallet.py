"""Membership/permanent generation accounting against isolated PostgreSQL."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from app.core.errors import Conflict, QuotaExceeded
from app.models.billing import (
    BillingOrder,
    BillingProvider,
    CreditAccount,
    CreditEntry,
    CreditReservation,
    MembershipAllocation,
    MembershipPlan,
    MembershipTerm,
)
from app.models.generation import GenerationJob
from app.modules.billing import wallet
from tests.test_media_workflow import studio  # noqa: F401


def job(db, ids):
    row = GenerationJob(
        project_id=uuid.UUID(ids["project"]),
        target_id=uuid.UUID(ids["shot"]),
        provider="mock",
        created_by=ids["user"],
    )
    db.add(row)
    db.flush()
    return row.id


def term(db, ids, units, *, start=-1, end=1, status="granted"):
    moment = datetime.now(UTC)
    plan = MembershipPlan(
        name="Wallet test",
        currency="cny",
        amount_minor=100,
        credits=units,
        duration=1,
        duration_unit="month",
    )
    provider = BillingProvider(
        name="Offline wallet fixture", kind="stripe", currency="cny", config_enc=b"unused"
    )
    db.add_all([plan, provider])
    db.flush()
    order = BillingOrder(
        user_id=ids["user"],
        plan_id=plan.id,
        membership_days=30,
        provider_id=provider.id,
        idempotency_key=uuid.uuid4(),
        pack_name=plan.name,
        provider_name=provider.name,
        public_origin="https://example.com",
        currency="cny",
        amount_minor=100,
        credits=units,
        status="completed",
        expires_at=moment,
        next_check_at=moment,
    )
    db.add(order)
    db.flush()
    row = MembershipTerm(
        order_id=order.id,
        user_id=ids["user"],
        plan_id=plan.id,
        starts_at=moment + timedelta(days=start),
        expires_at=moment + timedelta(days=end),
        credits=units,
        available=units,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def fund(db, ids, units):
    wallet.change(db, ids["user"], "wallet_test_funding", uuid.uuid4(), available=units)


def balance(db, ids):
    account = db.get(CreditAccount, ids["user"], populate_existing=True)
    return account.available, account.reserved


def allocations(db, job_id):
    return list(
        db.scalars(select(MembershipAllocation).where(MembershipAllocation.job_id == job_id))
    )


def entries(db, job_id):
    return {
        entry.kind: (entry.available_delta, entry.reserved_delta)
        for entry in db.scalars(select(CreditEntry).where(CreditEntry.reference_id == job_id))
    }


@pytest.mark.parametrize("used", [80, 180])
def test_earliest_expiry_and_membership_first_settlement_preserve_permanent_ledger(studio, used):
    _, sessions, ids = studio
    with sessions() as db:
        fund(db, ids, 200)
        later = term(db, ids, 100, start=-3, end=5)
        earlier = term(db, ids, 60, end=2)
        excluded = [
            term(db, ids, 100, start=2, end=32),
            term(db, ids, 100, start=-32, end=-2),
            term(db, ids, 100, status="refund_pending"),
            term(db, ids, 100, status="refunded"),
        ]
        job_id = job(db, ids)
        wallet.reserve_generation(db, ids["user"], job_id, 190)
        wallet.reserve_generation(db, ids["user"], job_id, 190)
        assert balance(db, ids) == (170, 30)
        assert {a.order_id: a.units for a in allocations(db, job_id)} == {
            earlier.order_id: 60,
            later.order_id: 100,
        }
        assert all((row.available, row.reserved) == (100, 0) for row in excluded)
        wallet.settle_generation(db, job_id, used)
        wallet.settle_generation(db, job_id, used)
        permanent_used = max(0, used - 160)
        assert balance(db, ids) == (200 - permanent_used, 0)
        assert (earlier.available, earlier.reserved) == (0, 0)
        assert (later.available, later.reserved) == (max(0, 160 - used), 0)
        assert {a.order_id: a.settled_units for a in allocations(db, job_id)} == {
            earlier.order_id: 60,
            later.order_id: min(used - 60, 100),
        }
        assert entries(db, job_id) == {
            "generation_reserve": (-30, 30),
            "generation_settle": (30 - permanent_used, -30),
        }
        with pytest.raises(Conflict):
            wallet.settle_generation(db, job_id, used + 1)
        db.commit()


@pytest.mark.parametrize("used", [0, 30, 100])
def test_membership_only_never_changes_permanent_wallet(studio, used):
    _, sessions, ids = studio
    with sessions() as db:
        row = term(db, ids, 100)
        job_id = job(db, ids)
        wallet.reserve_generation(db, ids["user"], job_id, 100)
        assert balance(db, ids) == (0, 0)
        if used:
            wallet.settle_generation(db, job_id, used)
        else:
            wallet.release_generation(db, job_id)
            wallet.release_generation(db, job_id)
        assert (row.available, row.reserved) == (100 - used, 0)
        assert balance(db, ids) == (0, 0)
        assert entries(db, job_id) == {}
        assert allocations(db, job_id)[0].settled_units == used
        db.commit()


@pytest.mark.parametrize("used", [0, 30])
def test_returned_expired_allocation_never_becomes_spendable_again(studio, used):
    _, sessions, ids = studio
    with sessions() as db:
        row = term(db, ids, 100, start=-2)
        job_id = job(db, ids)
        wallet.reserve_generation(db, ids["user"], job_id, 80)
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.flush()
        if used:
            wallet.settle_generation(db, job_id, used)
        else:
            wallet.release_generation(db, job_id)
        assert (row.available, row.reserved) == (100 - used, 0)
        next_job = job(db, ids)
        with pytest.raises(QuotaExceeded):
            wallet.reserve_generation(db, ids["user"], next_job, 1)
        assert db.get(CreditReservation, next_job) is None
        assert balance(db, ids) == (0, 0)
        db.commit()


def test_insufficient_combined_funds_roll_back_holds_even_if_caller_commits(studio):
    _, sessions, ids = studio
    with sessions() as db:
        fund(db, ids, 25)
        row = term(db, ids, 40)
        order_id = row.order_id
        job_id = job(db, ids)
        with pytest.raises(QuotaExceeded):
            wallet.reserve_generation(db, ids["user"], job_id, 100)
        db.commit()
    with sessions() as db:
        row = db.get(MembershipTerm, order_id)
        assert (row.available, row.reserved) == (40, 0)
        assert balance(db, ids) == (25, 0)
        assert db.get(CreditReservation, job_id) is None
        assert not allocations(db, job_id) and not entries(db, job_id)


def test_mixed_release_returns_each_source_and_rejects_reusing_released_job(studio):
    _, sessions, ids = studio
    with sessions() as db:
        fund(db, ids, 200)
        row = term(db, ids, 100)
        job_id = job(db, ids)
        wallet.reserve_generation(db, ids["user"], job_id, 150)
        wallet.release_generation(db, job_id)
        wallet.release_generation(db, job_id)
        assert (row.available, row.reserved) == (100, 0)
        assert balance(db, ids) == (200, 0)
        assert entries(db, job_id) == {
            "generation_reserve": (-50, 50),
            "generation_release": (50, -50),
        }
        with pytest.raises(Conflict):
            wallet.reserve_generation(db, ids["user"], job_id, 150)
        with pytest.raises(Conflict):
            wallet.settle_generation(db, job_id, 100)
        db.commit()


def test_failed_extra_settlement_preserves_the_original_hold(studio):
    _, sessions, ids = studio
    with sessions() as db:
        fund(db, ids, 20)
        row = term(db, ids, 100)
        order_id = row.order_id
        job_id = job(db, ids)
        wallet.reserve_generation(db, ids["user"], job_id, 80)
        with pytest.raises(QuotaExceeded):
            wallet.settle_generation(db, job_id, 120)
        db.commit()
    with sessions() as db:
        row = db.get(MembershipTerm, order_id)
        assert (row.available, row.reserved) == (20, 80)
        assert balance(db, ids) == (20, 0)
        assert db.get(CreditReservation, job_id).status == "reserved"
        assert allocations(db, job_id)[0].settled_units is None
        wallet.settle_generation(db, job_id, 70)
        assert (row.available, row.reserved) == (30, 0)
        assert balance(db, ids) == (20, 0)
        db.commit()


@pytest.mark.parametrize("used", [None, 70, 120])
def test_legacy_reservation_remains_permanent_even_after_membership_purchase(studio, used):
    _, sessions, ids = studio
    with sessions() as db:
        fund(db, ids, 200)
        job_id = job(db, ids)
        wallet.change(db, ids["user"], "generation_reserve", job_id, available=-100, reserved=100)
        db.add(CreditReservation(job_id=job_id, user_id=ids["user"], units=100))
        db.flush()
        row = term(db, ids, 100)
        if used is None:
            wallet.release_generation(db, job_id)
            wallet.release_generation(db, job_id)
        else:
            wallet.settle_generation(db, job_id, used)
            wallet.settle_generation(db, job_id, used)
        assert (row.available, row.reserved) == (100, 0)
        assert balance(db, ids) == (200 - (used or 0), 0)
        assert not allocations(db, job_id)
        assert db.get(CreditReservation, job_id).units == 100
        db.commit()


def test_concurrent_duplicate_reservations_hold_each_source_once(studio):
    _, sessions, ids = studio
    with sessions() as db:
        fund(db, ids, 100)
        row = term(db, ids, 100)
        order_id = row.order_id
        job_id = job(db, ids)
        db.commit()
    ready = Barrier(2)

    def reserve():
        with sessions() as db:
            ready.wait(timeout=5)
            wallet.reserve_generation(db, ids["user"], job_id, 150)
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(reserve) for _ in range(2)]
        for task in tasks:
            task.result(timeout=15)
    with sessions() as db:
        row = db.get(MembershipTerm, order_id)
        assert (row.available, row.reserved) == (0, 100)
        assert balance(db, ids) == (50, 50)
        assert len(allocations(db, job_id)) == 1
        assert entries(db, job_id) == {"generation_reserve": (-50, 50)}


def test_concurrent_jobs_cannot_spend_the_same_membership_allowance(studio):
    _, sessions, ids = studio
    with sessions() as db:
        fund(db, ids, 25)
        row = term(db, ids, 100)
        order_id = row.order_id
        job_ids = [job(db, ids), job(db, ids)]
        db.commit()
    ready = Barrier(2)

    def reserve(job_id):
        with sessions() as db:
            ready.wait(timeout=5)
            try:
                wallet.reserve_generation(db, ids["user"], job_id, 100)
            except QuotaExceeded:
                result = "insufficient"
            else:
                result = "reserved"
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(reserve, job_id) for job_id in job_ids]
        assert sorted(task.result(timeout=15) for task in tasks) == ["insufficient", "reserved"]
    with sessions() as db:
        row = db.get(MembershipTerm, order_id)
        assert (row.available, row.reserved) == (0, 100)
        assert balance(db, ids) == (25, 0)
        assert len(list(db.scalars(select(CreditReservation)))) == 1
        assert len(list(db.scalars(select(MembershipAllocation)))) == 1
