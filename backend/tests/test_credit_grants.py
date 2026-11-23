"""Complimentary credit provenance, onboarding and administration on real PostgreSQL."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.errors import Conflict, QuotaExceeded
from app.models.billing import (
    BillingSettings,
    CreditAccount,
    CreditAdjustment,
    CreditEntry,
    CreditReservation,
)
from app.models.identity import AuditLog, User
from app.modules.billing import wallet
from app.modules.identity import google, schemas, service
from tests.test_billing_orders import checkout, commerce, notify, setup_sales  # noqa: F401
from tests.test_billing_wallet import job, term
from tests.test_media_workflow import studio  # noqa: F401

API = "/api/v1/admin/billing"


def gift(
    client, user_id, points="2.50", *, operation="grant", key=None, reason="Manual test grant"
):
    return client.post(
        f"{API}/credits/{user_id}/adjustments",
        json={
            "idempotency_key": str(key or uuid.uuid4()),
            "operation": operation,
            "points": points,
            "reason": reason,
        },
    )


def balances(db, user_id):
    return wallet.balance_view(db.get(CreditAccount, user_id, populate_existing=True))


def test_admin_lookup_permissions_and_adjustment_audit_idempotency(commerce):
    client, sessions, ids, _, other = commerce
    uid = ids["user"]
    assert client.get(API + "/credits", headers=other).status_code == 403
    assert client.get(API + f"/credits/{uid}", headers=other).status_code == 403
    assert client.get(API + "/credit-settings", headers=other).status_code == 403
    payload = {
        "operation": "grant",
        "idempotency_key": str(uuid.uuid4()),
        "points": "2.50",
        "reason": "test",
    }
    assert (
        client.post(API + f"/credits/{uid}/adjustments", json=payload, headers=other).status_code
        == 403
    )
    assert (
        client.patch(
            API + "/credit-settings", json={"welcome_points": "100"}, headers=other
        ).status_code
        == 403
    )
    result = client.get(API + "/credits", params={"search": "  MEDIA@EXAMPLE.COM "})
    assert result.status_code == 200 and [row["user_id"] for row in result.json()] == [str(uid)]
    assert result.json()[0]["grant_available"] == 0
    assert client.get(API + "/credits", params={"search": "media@example"}).json() == []
    with sessions() as db:
        wallet.change(db, uid, "purchase_fixture", uuid.uuid4(), available=500)
        db.commit()
    key = uuid.uuid4()
    first = gift(client, uid, key=key)
    assert first.status_code == 200, first.text
    assert (first.json()["available"], first.json()["grant_available"]) == (750, 250)
    assert gift(client, uid, key=key).json()["adjustment"]["id"] == first.json()["adjustment"]["id"]
    assert gift(client, uid, key=key, reason="different request").status_code == 409
    revoked = gift(client, uid, "1", operation="revoke")
    assert (revoked.json()["available"], revoked.json()["grant_available"]) == (650, 150)
    assert gift(client, uid, "1.51", operation="revoke").status_code == 409
    # Retrying an old grant after a revoke cannot issue it again.
    assert gift(client, uid, key=key).json()["grant_available"] == 150
    detail = client.get(API + f"/credits/{uid}").json()
    assert len(detail["adjustments"]) == 2
    with sessions() as db:
        rows = list(db.scalars(select(AuditLog).where(AuditLog.action.like("billing.credit_%"))))
        assert len(rows) == 2 and all(row.user_id == uid for row in rows)
        assert all(row.detail["reason"] == "Manual test grant" for row in rows)
        assert db.scalar(select(func.count()).select_from(CreditAdjustment)) == 2
    wallet_view = client.get("/api/v1/billing/wallet").json()
    assert wallet_view["grant_available"] == 150 and wallet_view["reserved"] == 0
    assert all("grant_available_delta" in row for row in wallet_view["entries"])


@pytest.mark.parametrize(
    "points", ["0", "-1", "1.001", "1e2", "NaN", "Infinity", "100000000.01", 1, True]
)
def test_admin_rejects_inexact_or_invalid_grants(commerce, points):
    client, _, ids, _, _ = commerce
    assert gift(client, ids["user"], points).status_code == 422


def test_adjustment_missing_disabled_user_and_required_reason(commerce):
    client, sessions, ids, _, _ = commerce
    assert gift(client, uuid.uuid4()).status_code == 404
    assert gift(client, ids["user"], reason="   ").status_code == 422
    gift(client, ids["user"])
    with sessions() as db:
        user = User(username="disabled_target", email="disabled@example.com", display_name="Off")
        db.add(user)
        db.flush()
        wallet.adjust(db, user.id, ids["user"], uuid.uuid4(), "grant", 250, "before disable")
        user.is_active = False
        db.commit()
        uid = user.id
    assert gift(client, uid).status_code == 409
    assert gift(client, uid, "1", operation="revoke").status_code == 200


def test_reservations_preserve_grant_source_and_protect_paid_and_member_credit(commerce):
    client, sessions, ids, _, _ = commerce
    uid = ids["user"]
    assert gift(client, uid, "2").status_code == 200
    with sessions() as db:
        wallet.change(db, uid, "purchase_fixture", uuid.uuid4(), available=300)
        member = term(db, ids, 100)
        jid = job(db, ids)
        wallet.reserve_generation(db, uid, jid, 450)
        assert balances(db, uid) == {
            "available": 150,
            "reserved": 350,
            "grant_available": 0,
            "grant_reserved": 200,
        }
        assert db.get(CreditReservation, jid).grant_units == 200
        with pytest.raises(Conflict):
            wallet.adjust(db, uid, uid, uuid.uuid4(), "revoke", 1, "cannot reclaim reserved")
        wallet.settle_generation(db, jid, 250)
        wallet.settle_generation(db, jid, 250)
        assert balances(db, uid) == {
            "available": 350,
            "reserved": 0,
            "grant_available": 50,
            "grant_reserved": 0,
        }
        assert member.available == member.reserved == 0
        wallet.adjust(db, uid, uid, uuid.uuid4(), "revoke", 50, "only unused gift")
        assert balances(db, uid)["available"] == 300
        with pytest.raises(Conflict):
            wallet.adjust(db, uid, uid, uuid.uuid4(), "revoke", 1, "paid credit is protected")
        db.commit()


def test_release_and_actual_overage_never_consume_other_reservations(commerce):
    client, sessions, ids, _, _ = commerce
    uid = ids["user"]
    gift(client, uid, "3")
    with sessions() as db:
        wallet.change(db, uid, "purchase_fixture", uuid.uuid4(), available=500)
        first, second = job(db, ids), job(db, ids)
        wallet.reserve_generation(db, uid, first, 100)
        wallet.reserve_generation(db, uid, second, 100)
        # Actual 250 consumes 100 held grants + 100 free grants + 50 purchased.
        wallet.settle_generation(db, first, 250)
        assert balances(db, uid) == {
            "available": 450,
            "reserved": 100,
            "grant_available": 0,
            "grant_reserved": 100,
        }
        wallet.release_generation(db, second)
        wallet.release_generation(db, second)
        assert balances(db, uid) == {
            "available": 550,
            "reserved": 0,
            "grant_available": 100,
            "grant_reserved": 0,
        }


def test_shortage_detail_rolls_back_membership_and_gift_reservations(commerce):
    client, sessions, ids, _, _ = commerce
    uid = ids["user"]
    gift(client, uid, "1")
    with sessions() as db:
        active = term(db, ids, 50)
        term(db, ids, 1000, start=1, end=31)
        term(db, ids, 1000, start=-31, end=-1)
        jid = job(db, ids)
        with pytest.raises(QuotaExceeded) as caught:
            wallet.reserve_generation(db, uid, jid, 175)
        assert caught.value.detail == {
            "reason": "insufficient_personal_credits",
            "required_units": 175,
            "available_units": 150,
            "shortfall_units": 25,
        }
        assert balances(db, uid) == {
            "available": 100,
            "reserved": 0,
            "grant_available": 100,
            "grant_reserved": 0,
        }
        db.refresh(active)
        assert (active.available, active.reserved) == (50, 0)
        assert db.get(CreditReservation, jid) is None
        db.commit()  # Failure caught by a caller must not leave partial holds.


def test_paid_refund_cannot_be_funded_from_gifts_and_failure_restores_paid_source(commerce):
    client, sessions, ids, _, _ = commerce
    uid = ids["user"]
    gift(client, uid, "5")
    with sessions() as db:
        wallet.change(db, uid, "purchase_fixture", uuid.uuid4(), available=200)
        with pytest.raises(QuotaExceeded):
            wallet.change(db, uid, "refund_reserve", uuid.uuid4(), available=-201, reserved=201)
        rid = uuid.uuid4()
        wallet.change(db, uid, "refund_reserve", rid, available=-200, reserved=200)
        assert balances(db, uid) == {
            "available": 500,
            "reserved": 200,
            "grant_available": 500,
            "grant_reserved": 0,
        }
        wallet.adjust(db, uid, uid, uuid.uuid4(), "revoke", 500, "reclaim available gift")
        wallet.change(db, uid, "refund_release", rid, available=200, reserved=-200)
        assert balances(db, uid) == {
            "available": 200,
            "reserved": 0,
            "grant_available": 0,
            "grant_reserved": 0,
        }


@pytest.mark.parametrize("paid_was_spent", [False, True])
def test_actual_refund_api_preserves_gifts_and_cannot_cash_them_out(commerce, paid_was_spent):
    client, sessions, ids, fake, _ = commerce
    pack, provider = setup_sales(client)
    order = checkout(client, pack, provider)
    assert notify(client, provider, order).status_code == 204
    if paid_was_spent:
        with sessions() as db:
            jid = job(db, ids)
            wallet.reserve_generation(db, ids["user"], jid, pack["credits"])
            wallet.settle_generation(db, jid, pack["credits"])
            db.commit()
    assert gift(client, ids["user"], "200").status_code == 200
    fake.refund_status = "succeeded"
    response = client.post(
        API + f"/orders/{order['id']}/refunds",
        json={"amount_minor": 103, "idempotency_key": str(uuid.uuid4()), "reason": "refund test"},
    )
    assert response.status_code == (402 if paid_was_spent else 200), response.text
    assert len(fake.refunded) == (0 if paid_was_spent else 1)
    assert client.get("/api/v1/billing/wallet").json()["grant_available"] == 20000


def test_credit_settings_and_legacy_checkout_settings_do_not_overwrite_each_other(commerce):
    client, _, _, _, _ = commerce
    setup_sales(client)
    response = client.patch(API + "/credit-settings", json={"welcome_points": "25.5"})
    assert response.status_code == 200 and response.json()["welcome_credits"] == 2550
    assert client.get(API + "/settings").json()["enabled"] is True
    legacy = {
        "enabled": False,
        "payment_required": True,
        "public_origin": "https://example.com",
        "checkout_minutes": 60,
    }
    assert client.patch(API + "/settings", json=legacy).status_code == 200
    assert client.get(API + "/credit-settings").json()["welcome_credits"] == 2550


def test_concurrent_same_adjustment_and_reclaims_are_serialized(commerce):
    _, sessions, ids, _, _ = commerce
    uid, key = ids["user"], uuid.uuid4()
    barrier = Barrier(2)

    def apply(operation, units, request_key):
        with sessions() as db:
            barrier.wait(timeout=10)
            try:
                row = wallet.adjust(db, uid, uid, request_key, operation, units, "concurrent test")
                db.commit()
                return str(row.id)
            except Conflict:
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply, "grant", 200, key) for _ in range(2)]
        assert len({item.result(timeout=20) for item in futures}) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply, "revoke", 150, uuid.uuid4()) for _ in range(2)]
        assert sum(item.result(timeout=20) == "conflict" for item in futures) == 1
    with sessions() as db:
        assert balances(db, uid)["grant_available"] == 50
        assert db.scalar(select(func.count()).select_from(CreditAdjustment)) == 2


def test_welcome_policy_defaults_disabled_and_password_signup_is_one_time(commerce):
    client, sessions, ids, _, _ = commerce
    assert client.get(API + "/credit-settings").json() == {
        "welcome_points": "0",
        "welcome_credits": 0,
    }
    configured = client.patch(API + "/credit-settings", json={"welcome_points": "100.00"})
    assert configured.json() == {"welcome_points": "100", "welcome_credits": 10000}
    with sessions() as db:
        assert db.get(CreditAccount, ids["user"]) is None  # Never backfill existing accounts.
    payload = {"email": "gift-new@example.com", "password": "safe-password123"}
    registered = client.post("/api/v1/auth/register", json=payload)
    assert registered.status_code == 200, registered.text
    uid = uuid.UUID(registered.json()["id"])
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409
    client.patch(API + "/credit-settings", json={"welcome_points": "200"})
    with sessions() as db:
        wallet.grant_welcome(db, uid)
        wallet.grant_welcome(db, uid)
        assert balances(db, uid)["grant_available"] == 10000
        assert db.get(CreditAccount, uid).welcome_processed_at is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(CreditEntry)
                .where(CreditEntry.kind == "welcome_grant")
            )
            == 1
        )
        db.commit()
    login = client.post("/api/v1/auth/login", json=payload)
    assert login.status_code == 200
    with sessions() as db:
        assert balances(db, uid)["grant_available"] == 10000


def test_zero_policy_is_final_for_onboarding_and_registration_rollback_is_atomic(commerce):
    _, sessions, _, _, _ = commerce
    with sessions() as db:
        user = service.register_user(
            db, schemas.RegisterIn(email="zero@example.com", password="safe-password123")
        )
        uid = user.id
        db.commit()
        db.add(BillingSettings(id=1, welcome_credits=1234))
        db.flush()
        wallet.grant_welcome(db, uid)
        assert balances(db, uid)["grant_available"] == 0
        user2 = service.register_user(
            db, schemas.RegisterIn(email="rollback@example.com", password="safe-password123")
        )
        uid2 = user2.id
        assert balances(db, uid2)["grant_available"] == 1234
        db.rollback()
    with sessions() as db:
        assert db.get(User, uid2) is None and db.get(CreditAccount, uid2) is None


def test_google_signup_grants_once_but_linking_existing_user_never_grants(commerce, monkeypatch):
    _, sessions, ids, _, _ = commerce
    monkeypatch.setattr(settings, "google_registration_enabled", True)
    identity = google.VerifiedGoogleIdentity(
        subject="new-gift-google",
        email="google-gift@example.com",
        email_verified=True,
        name="Writer",
    )
    with sessions() as db:
        db.add(BillingSettings(id=1, welcome_credits=250))
        db.flush()
        user = google.resolve_user(db, identity, {"mode": "login"})
        uid = user.id
        assert balances(db, uid)["grant_available"] == 250
        assert google.resolve_user(db, identity, {"mode": "login"}).id == uid
        existing = db.get(User, ids["user"])
        linked = google.VerifiedGoogleIdentity(
            subject="existing-gift-google",
            email="other-linked@example.com",
            email_verified=True,
            name="Existing",
        )
        assert (
            google.resolve_user(
                db,
                linked,
                {
                    "mode": "link",
                    "user_id": str(existing.id),
                    "auth_version": existing.auth_version,
                },
            ).id
            == existing.id
        )
        assert db.get(CreditAccount, existing.id) is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(CreditEntry)
                .where(CreditEntry.kind == "welcome_grant")
            )
            == 1
        )


@pytest.mark.parametrize("points", ["-1", "10000.01", "1e2", "1.001", None, 5])
def test_welcome_settings_validate_exact_bounded_points(commerce, points):
    client, _, _, _, _ = commerce
    assert (
        client.patch(API + "/credit-settings", json={"welcome_points": points}).status_code == 422
    )


def test_database_prevents_grants_exceeding_total_balance(commerce):
    _, sessions, ids, _, _ = commerce
    with sessions() as db:
        account = wallet.lock_account(db, ids["user"])
        with pytest.raises(IntegrityError), db.begin_nested():
            account.grant_available = 1
            db.flush()
