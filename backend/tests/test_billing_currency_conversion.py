"""Manual membership FX quotes against isolated PostgreSQL; no live payment calls."""

# ruff: noqa: F811

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.models.billing import BillingOrder, BillingRefund, BillingSettings, MembershipTerm
from app.modules.billing import orders
from tests.test_billing_orders import (  # noqa: F401
    API,
    checkout,
    commerce,
    endpoint,
    membership_checkout,
    notify,
    plan_payload,
    read_wallet,
    setup_sales,
)
from tests.test_library_migrations import library_migration_schema  # noqa: F401
from tests.test_media_workflow import studio  # noqa: F401

SETTINGS = "/admin/billing/settings"
RATE = "subscription_usd_to_cny_rate"


def preferences(**changes):
    return {
        "enabled": False,
        "payment_required": False,
        "public_origin": "https://example.com",
        "checkout_minutes": 60,
        **changes,
    }


def cny_channel(client, *, currency="cny", active=True):
    return endpoint(
        client,
        "POST",
        "/admin/billing/providers",
        {
            "name": "Isolated FX Stripe",
            "kind": "stripe",
            "currency": currency,
            "active": active,
            "config": {"secret_key": "sk_test_fixture", "webhook_secret": "whsec_fixture"},
        },
    )


def usd_plan(client, **changes):
    return endpoint(
        client,
        "POST",
        "/admin/billing/plans",
        plan_payload(currency="usd", amount_minor=101, original_amount_minor=None, **changes),
    )


def quoted_request(plan, provider, **changes):
    return {
        "plan_id": plan["id"],
        "provider_id": provider["id"],
        "idempotency_key": str(uuid.uuid4()),
        **changes,
    }


def enable_conversion(client, rate="7.5"):
    return endpoint(client, "PATCH", SETTINGS, preferences(enabled=True, **{RATE: rate}))


def test_rate_is_optional_canonical_and_preserved_for_legacy_settings_clients(commerce):
    client, sessions, _, _, other = commerce
    assert endpoint(client, "GET", SETTINGS)[RATE] is None
    with sessions() as db:
        assert db.get(BillingSettings, 1) is None  # Unsaved settings fallback.
    assert endpoint(client, "PATCH", SETTINGS, preferences(**{RATE: "007.500000"}))[RATE] == "7.5"
    assert endpoint(client, "PATCH", SETTINGS, preferences(checkout_minutes=70))[RATE] == "7.5"
    assert endpoint(client, "GET", SETTINGS)[RATE] == "7.5"
    with sessions() as db:
        assert db.get(BillingSettings, 1).subscription_usd_to_cny_rate == Decimal("7.5")
    response = client.patch(API + SETTINGS, json=preferences(**{RATE: "8"}), headers=other)
    assert response.status_code == 403
    assert endpoint(client, "GET", SETTINGS)[RATE] == "7.5"
    for disabled in ("0", None):
        assert endpoint(client, "PATCH", SETTINGS, preferences(**{RATE: disabled}))[RATE] is None
        assert endpoint(client, "GET", SETTINGS)[RATE] is None
        endpoint(client, "PATCH", SETTINGS, preferences(**{RATE: "7.5"}))


def test_invalid_rates_fail_without_overwriting_saved_settings(commerce):
    client, _, _, _, _ = commerce
    endpoint(client, "PATCH", SETTINGS, preferences(**{RATE: "7.25"}))
    for invalid in ("-1", "NaN", "Infinity", "1e2", "", "1000000", "0.0000001", 7.5, True):
        response = client.patch(API + SETTINGS, json=preferences(**{RATE: invalid}))
        assert response.status_code == 422, (invalid, response.text)
        assert endpoint(client, "GET", SETTINGS)[RATE] == "7.25"


def test_legacy_first_save_does_not_overwrite_concurrent_rate_insert(commerce, monkeypatch):
    client, sessions, _, _, _ = commerce
    with sessions() as db:
        assert db.get(BillingSettings, 1) is None
    legacy_read = Event()
    resume_legacy = Event()
    original = orders.public_checkout_origin

    def pause_legacy_after_settings_read(value):
        if value == "https://legacy.example.com":
            legacy_read.set()
            assert resume_legacy.wait(10), "Concurrent settings writer did not finish"
        return original(value)

    monkeypatch.setattr(orders, "public_checkout_origin", pause_legacy_after_settings_read)
    with ThreadPoolExecutor(max_workers=1) as executor:
        legacy = executor.submit(
            endpoint,
            client,
            "PATCH",
            SETTINGS,
            preferences(public_origin="https://legacy.example.com", checkout_minutes=75),
        )
        try:
            assert legacy_read.wait(10), "Legacy request did not reach its read-before-write window"
            assert (
                endpoint(client, "PATCH", SETTINGS, preferences(**{RATE: "8.25"}))[RATE] == "8.25"
            )
        finally:
            resume_legacy.set()
        saved = legacy.result(timeout=10)
    assert saved[RATE] == "8.25"  # Response reflects the persisted winning rate, not stale null.
    actual = endpoint(client, "GET", SETTINGS)
    assert actual[RATE] == "8.25" and actual["checkout_minutes"] == 75


def test_cny_only_channel_requires_rate_to_sell_usd_only_membership(commerce):
    client, sessions, _, fake, _ = commerce
    plan, provider = usd_plan(client), cny_channel(client)
    response = client.patch(API + SETTINGS, json=preferences(enabled=True))
    assert response.status_code == 422
    assert not endpoint(client, "GET", "/billing/catalog")["enabled"]
    endpoint(client, "PATCH", SETTINGS, preferences(**{RATE: "7.5"}))
    # Omission must use the stored rate when checking whether sales can be enabled.
    assert endpoint(client, "PATCH", SETTINGS, preferences(enabled=True))[RATE] == "7.5"
    catalog = endpoint(client, "GET", "/billing/catalog")
    assert len(catalog["plans"]) == 1 and catalog["packs"] == []
    offered = catalog["plans"][0]
    assert offered["id"] == plan["id"]
    assert (offered["currency"], offered["amount_minor"]) == ("usd", 101)
    assert offered["payment_amounts"] == {"cny": 758}
    assert catalog["providers"][0]["id"] == provider["id"]
    for expected in (None, 101, 757, 759):
        payload = quoted_request(plan, provider)
        if expected is not None:
            payload["expected_amount_minor"] = expected
        response = client.post(API + "/billing/orders", json=payload)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PAYMENT_QUOTE_CHANGED"
    assert fake.created == []
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(BillingOrder)) == 0
    # Disabling conversion cannot leave the only product/channel pair falsely sellable.
    assert (
        client.patch(API + SETTINGS, json=preferences(enabled=True, **{RATE: None})).status_code
        == 422
    )
    assert endpoint(client, "GET", SETTINGS)[RATE] == "7.5"


@pytest.mark.parametrize("rate,expected", [("7.5", 758), ("7.499999", 757), ("0.005", 1)])
def test_converted_checkout_rounds_half_up_and_sends_actual_cny(
    commerce, monkeypatch, rate, expected
):
    client, _, _, fake, _ = commerce
    plan, provider = usd_plan(client), cny_channel(client)
    enable_conversion(client, rate)
    requests = []
    create = fake.create_checkout

    def capture(request):
        requests.append(request)
        return create(request)

    monkeypatch.setattr(fake, "create_checkout", capture)
    order = endpoint(
        client,
        "POST",
        "/billing/orders",
        quoted_request(plan, provider, expected_amount_minor=expected),
    )
    assert (order["currency"], order["amount_minor"]) == ("cny", expected)
    assert order["conversion"] == {
        "original_amount_minor": 101,
        "original_currency": "usd",
        "rate": rate,
    }
    assert order["credits"] == plan["credits"] and order["membership_days"] == 30
    assert len(requests) == 1
    assert (requests[0].currency, requests[0].amount_minor) == ("cny", expected)


def test_stale_quotes_reject_new_orders_but_retries_preserve_existing_snapshot(commerce):
    client, _, _, fake, _ = commerce
    plan, provider = usd_plan(client), cny_channel(client)
    enable_conversion(client)
    payload = quoted_request(plan, provider, expected_amount_minor=758)
    fake.checkout_fails = True
    original = endpoint(client, "POST", "/billing/orders", payload)
    assert original["checkout_url"] is None
    enable_conversion(client, "8")
    assert endpoint(client, "GET", "/billing/catalog")["plans"][0]["payment_amounts"] == {
        "cny": 808
    }
    stale = client.post(
        API + "/billing/orders", json={**payload, "idempotency_key": str(uuid.uuid4())}
    )
    assert stale.status_code == 409
    fake.checkout_fails = False
    retry = endpoint(client, "POST", "/billing/orders", payload)
    assert retry["id"] == original["id"] and retry["amount_minor"] == 758
    assert retry["conversion"] == original["conversion"]
    assert fake.created == [original["id"], original["id"]]
    without_expected = {
        key: value for key, value in payload.items() if key != "expected_amount_minor"
    }
    assert endpoint(client, "POST", "/billing/orders", without_expected)["id"] == original["id"]
    fresh = endpoint(
        client, "POST", "/billing/orders", quoted_request(plan, provider, expected_amount_minor=808)
    )
    assert fresh["conversion"]["rate"] == "8" and fresh["amount_minor"] == 808


def test_callback_grant_and_refund_keep_cny_snapshot_after_rate_changes(commerce, monkeypatch):
    client, sessions, _, fake, _ = commerce
    plan, provider = usd_plan(client), cny_channel(client)
    enable_conversion(client)
    order = endpoint(
        client, "POST", "/billing/orders", quoted_request(plan, provider, expected_amount_minor=758)
    )
    enable_conversion(client, "8")
    for overrides in ({"currency": "usd"}, {"amount_minor": 101}, {"amount_minor": 808}):
        assert notify(client, provider, order, **overrides).status_code == 409
    assert endpoint(client, "GET", "/billing/memberships") == []
    for _ in range(2):
        assert notify(client, provider, order).status_code == 204
    [term] = endpoint(client, "GET", "/billing/memberships")
    assert term["order_id"] == order["id"] and term["available"] == plan["credits"]
    assert read_wallet(client)["available"] == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(MembershipTerm)) == 1
    calls = []
    create_refund = fake.create_refund

    def capture(request, payment_id, refund_id, amount):
        calls.append((request.currency, request.amount_minor, amount))
        return create_refund(request, payment_id, refund_id, amount)

    monkeypatch.setattr(fake, "create_refund", capture)
    path = f"/admin/billing/orders/{order['id']}/refunds"
    refund_payload = {
        "reason": "isolated conversion refund",
        "idempotency_key": str(uuid.uuid4()),
        "amount_minor": 808,
    }
    assert client.post(API + path, json=refund_payload).status_code == 422
    pending = endpoint(client, "POST", path, {**refund_payload, "amount_minor": 758})
    assert pending["membership"]["status"] == "refund_pending"
    assert pending["membership"]["available"] == 0
    assert calls == [("cny", 758, 758)]
    endpoint(client, "PATCH", SETTINGS, preferences(**{RATE: None}))
    fake.refund_status = "succeeded"
    with sessions() as db:
        # The real reconciliation endpoint throttles polling; make this fixture due.
        db.get(BillingRefund, uuid.UUID(pending["refunds"][0]["id"])).next_check_at = datetime.now(
            UTC
        )
        db.commit()
    settled = endpoint(
        client, "POST", f"/admin/billing/refunds/{pending['refunds'][0]['id']}/check"
    )
    assert settled["refunded_minor"] == 758 and settled["currency"] == "cny"
    assert settled["conversion"] == order["conversion"]
    assert settled["membership"]["status"] == "refunded"
    assert len(fake.refunded) == 1


@pytest.mark.parametrize("rate,amount", [("0.000001", 1), ("999999.999999", 1000000000)])
def test_zero_or_overflow_conversion_cannot_enable_sales(commerce, rate, amount):
    client, _, _, fake, _ = commerce
    endpoint(
        client,
        "POST",
        "/admin/billing/plans",
        plan_payload(currency="usd", amount_minor=amount, original_amount_minor=None),
    )
    cny_channel(client)
    response = client.patch(API + SETTINGS, json=preferences(enabled=True, **{RATE: rate}))
    assert response.status_code == 422, response.text
    assert fake.created == []


def test_conversion_does_not_expand_point_packs_or_non_usd_memberships(commerce):
    client, _, _, _, _ = commerce
    pack, provider = setup_sales(client)
    legacy = checkout(client, pack, provider)
    plan = endpoint(client, "POST", "/admin/billing/plans", plan_payload())
    usd_pack = endpoint(
        client,
        "POST",
        "/admin/billing/packs",
        {"name": "USD points", "currency": "usd", "amount_minor": 101, "credits": 100},
    )
    euro_plan = endpoint(client, "POST", "/admin/billing/plans", plan_payload(currency="eur"))
    enable_conversion(client)
    catalog = endpoint(client, "GET", "/billing/catalog")
    assert [row["id"] for row in catalog["packs"]] == [pack["id"]]
    assert [row["id"] for row in catalog["plans"]] == [plan["id"]]
    for payload in (
        {"pack_id": usd_pack["id"]},
        {"plan_id": euro_plan["id"]},
    ):
        response = client.post(
            API + "/billing/orders",
            json={**payload, "provider_id": provider["id"], "idempotency_key": str(uuid.uuid4())},
        )
        assert response.status_code == 422
    same_currency = membership_checkout(client, plan, provider)
    assert same_currency["amount_minor"] == plan["amount_minor"]
    assert "conversion" not in same_currency
    historical = endpoint(client, "GET", f"/billing/orders/{legacy['id']}")
    assert historical["amount_minor"] == pack["amount_minor"] and "conversion" not in historical
    assert notify(client, provider, historical).status_code == 204
    assert read_wallet(client)["available"] == pack["credits"]


def test_native_usd_provider_remains_available_without_conversion_confirmation(commerce):
    client, _, _, _, _ = commerce
    plan = usd_plan(client)
    provider = cny_channel(client, currency="usd")
    cny_channel(client, active=False)
    enable_conversion(client)
    offered = endpoint(client, "GET", "/billing/catalog")["plans"][0]
    assert "payment_amounts" not in offered  # Only additional converted quotes are exposed.
    order = membership_checkout(client, plan, provider)
    assert (order["currency"], order["amount_minor"]) == ("usd", 101)
    assert "conversion" not in order


def test_disabling_rate_rejects_stale_confirmation_and_preserves_pending_order(commerce):
    client, sessions, _, _, _ = commerce
    _, provider = setup_sales(client)  # Keep native-CNY points available when FX is disabled.
    plan = usd_plan(client)
    enable_conversion(client)
    payload = quoted_request(plan, provider, expected_amount_minor=758)
    order = endpoint(client, "POST", "/billing/orders", payload)
    endpoint(client, "PATCH", SETTINGS, preferences(enabled=True, **{RATE: None}))
    assert endpoint(client, "GET", "/billing/catalog")["plans"] == []
    stale = client.post(
        API + "/billing/orders", json={**payload, "idempotency_key": str(uuid.uuid4())}
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "PAYMENT_QUOTE_CHANGED"
    retry = endpoint(client, "POST", "/billing/orders", payload)
    assert retry["id"] == order["id"] and retry["conversion"] == order["conversion"]
    assert notify(client, provider, order).status_code == 204
    with sessions() as db:
        # A historical stored zero also means disabled, even without an API write.
        db.get(BillingSettings, 1).subscription_usd_to_cny_rate = Decimal("0")
        db.commit()
    assert endpoint(client, "GET", SETTINGS)[RATE] is None
    assert endpoint(client, "GET", "/billing/catalog")["plans"] == []


def test_database_rejects_incomplete_or_inconsistent_conversion_snapshot(commerce):
    client, sessions, _, _, _ = commerce
    plan, provider = usd_plan(client), cny_channel(client)
    enable_conversion(client)
    order = endpoint(
        client,
        "POST",
        "/billing/orders",
        quoted_request(plan, provider, expected_amount_minor=758),
    )
    with sessions() as db:
        for changes in (
            {"original_amount_minor": None},
            {"conversion_rate": None},
            {"conversion_rate": Decimal("0")},
            {"amount_minor": 757},
            {"currency": "usd"},
        ):
            with pytest.raises(IntegrityError), db.begin_nested():
                row = db.get(BillingOrder, uuid.UUID(order["id"]))
                for field, value in changes.items():
                    setattr(row, field, value)
                db.flush()
        db.expire_all()
        row = db.get(BillingOrder, uuid.UUID(order["id"]))
        assert row.amount_minor == 758 and row.original_amount_minor == 101
        assert row.currency == "cny" and row.conversion_rate == Decimal("7.5")


def test_0068_migration_preserves_legacy_settings_and_order_amounts(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0067_membership_monthly_link")
    metadata = MetaData(schema=schema)
    tables = {
        name: Table(name, metadata, autoload_with=conn)
        for name in (
            "user",
            "billing_settings",
            "billing_pack",
            "billing_provider",
            "billing_order",
        )
    }
    uid, pack_id, provider_id, oid = (uuid.uuid4() for _ in range(4))
    conn.execute(
        tables["user"]
        .insert()
        .values(
            id=uid,
            username="fx_migration",
            email="migration@example.test",
            display_name="Migration",
            is_active=True,
        )
    )
    conn.execute(tables["billing_settings"].insert().values(id=1, **preferences(enabled=True)))
    conn.execute(
        tables["billing_pack"]
        .insert()
        .values(
            id=pack_id,
            name="Legacy",
            description="",
            currency="cny",
            amount_minor=103,
            credits=100,
            active=True,
        )
    )
    conn.execute(
        tables["billing_provider"]
        .insert()
        .values(
            id=provider_id,
            name="Legacy",
            kind="stripe",
            currency="cny",
            config_enc=b"fixture",
            active=True,
            allow_refunds=True,
        )
    )
    timestamp = datetime.now(UTC)
    conn.execute(
        tables["billing_order"]
        .insert()
        .values(
            id=oid,
            user_id=uid,
            pack_id=pack_id,
            provider_id=provider_id,
            idempotency_key=uuid.uuid4(),
            pack_name="Legacy",
            provider_name="Legacy",
            public_origin="https://example.com",
            currency="cny",
            amount_minor=103,
            credits=100,
            status="completed",
            expires_at=timestamp + timedelta(hours=1),
            next_check_at=timestamp,
            paid_at=timestamp,
            completed_at=timestamp,
            refunded_minor=0,
            refunded_credits=0,
        )
    )
    before = {
        name: conn.execute(select(tables[name])).mappings().one()
        for name in ("billing_settings", "billing_order")
    }
    command.upgrade(config, "0068_billing_currency_conversion")
    for name, old in before.items():
        assert conn.execute(select(tables[name])).mappings().one() == old
    migrated = MetaData(schema=schema)
    current_settings = Table("billing_settings", migrated, autoload_with=conn)
    current_orders = Table("billing_order", migrated, autoload_with=conn)
    assert conn.scalar(select(current_settings.c.subscription_usd_to_cny_rate)) is None
    row = conn.execute(select(current_orders)).mappings().one()
    assert row["original_amount_minor"] is None and row["conversion_rate"] is None
    command.downgrade(config, "0067_membership_monthly_link")
    for name, old in before.items():
        assert conn.execute(select(tables[name])).mappings().one() == old
    command.upgrade(config, "0068_billing_currency_conversion")
