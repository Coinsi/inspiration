"""Annual/monthly comparison metadata; real PostgreSQL, mocked payment provider."""

# ruff: noqa: F811

import uuid
from datetime import datetime

import pytest
from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.models.billing import MembershipPlan
from tests.test_billing_orders import (  # noqa: F401
    commerce,
    endpoint,
    membership_checkout,
    notify,
    setup_sales,
)
from tests.test_library_migrations import library_migration_schema  # noqa: F401
from tests.test_media_workflow import studio  # noqa: F401

PLANS = "/admin/billing/plans"


def payload(**changes):
    return {
        "name": "Monthly fixture",
        "currency": "cny",
        "amount_minor": 2500,
        "credits": 1000,
        "duration": 1,
        "duration_unit": "month",
        **changes,
    }


def yearly(monthly_id, **changes):
    return payload(
        name="Annual fixture",
        amount_minor=24900,
        credits=12000,
        duration=12,
        monthly_plan_id=monthly_id,
        **changes,
    )


def editable(view, **changes):
    return {key: value for key, value in {**view, **changes}.items() if key != "id"}


@pytest.mark.parametrize("duration,unit", [(12, "month"), (360, "day")])
def test_link_supports_real_monthly_plan_and_draft_preview(commerce, duration, unit):
    client, _, _, _, _ = commerce
    setup_sales(client)
    monthly = endpoint(
        client, "POST", PLANS, payload(active=False, duration=30, duration_unit="day")
    )
    data = yearly(monthly["id"])
    data.update(duration=duration, duration_unit=unit)
    annual = endpoint(client, "POST", PLANS, data)
    assert annual["monthly_plan_id"] == monthly["id"]
    assert annual["amount_minor"] == data["amount_minor"]
    assert annual["credits"] == data["credits"]
    catalog = endpoint(client, "GET", "/billing/catalog")
    assert [plan["id"] for plan in catalog["plans"]] == [annual["id"]]
    assert catalog["plans"][0]["monthly_plan_id"] == monthly["id"]
    admin = endpoint(client, "GET", PLANS)
    assert (
        next(plan for plan in admin if plan["id"] == annual["id"])["monthly_plan_id"]
        == monthly["id"]
    )


@pytest.mark.parametrize(
    "monthly_change,annual_change,status",
    [
        ({}, {"duration": 1}, 422),
        ({}, {"duration": 365, "duration_unit": "day"}, 422),
        ({}, {"duration": 24}, 422),
        ({"duration": 2}, {}, 422),
        ({"currency": "usd"}, {}, 422),
        ({}, {"credits": 11999}, 422),
        ({}, {"monthly_plan_id": str(uuid.uuid4())}, 404),
    ],
)
def test_incompatible_links_are_rejected_without_creating_plan(
    commerce, monthly_change, annual_change, status
):
    client, _, _, _, _ = commerce
    monthly = endpoint(client, "POST", PLANS, payload(**monthly_change))
    data = yearly(monthly["id"])
    data.update(annual_change)
    result = client.post("/api/v1" + PLANS, json=data)
    assert result.status_code == status, result.text
    assert len(endpoint(client, "GET", PLANS)) == 1


def test_omitted_link_preserves_and_explicit_null_clears(commerce):
    client, _, _, _, _ = commerce
    monthly = endpoint(client, "POST", PLANS, payload())
    annual = endpoint(client, "POST", PLANS, yearly(monthly["id"]))
    path = f"{PLANS}/{annual['id']}"
    legacy = editable(annual, name="Legacy client update")
    legacy.pop("monthly_plan_id")
    preserved = endpoint(client, "PATCH", path, legacy)
    assert preserved["monthly_plan_id"] == monthly["id"]
    cleared = endpoint(
        client, "PATCH", path, {**legacy, "monthly_plan_id": None, "duration": 1, "credits": 1000}
    )
    assert cleared["monthly_plan_id"] is None
    assert endpoint(client, "POST", PLANS, payload())["monthly_plan_id"] is None


def test_self_link_and_invalid_update_leave_previous_plan_unchanged(commerce):
    client, _, _, _, _ = commerce
    monthly = endpoint(client, "POST", PLANS, payload())
    annual = endpoint(client, "POST", PLANS, yearly(monthly["id"]))
    path = f"/api/v1{PLANS}/{annual['id']}"
    for change in ({"monthly_plan_id": annual["id"]}, {"credits": 11999}, {"currency": "usd"}):
        response = client.patch(path, json=editable(annual, **change))
        assert response.status_code == 422, response.text
        stored = next(row for row in endpoint(client, "GET", PLANS) if row["id"] == annual["id"])
        assert stored == annual


def test_target_edit_is_allowed_and_next_annual_edit_revalidates_current_target(commerce):
    client, _, _, _, _ = commerce
    monthly = endpoint(client, "POST", PLANS, payload())
    annual = endpoint(client, "POST", PLANS, yearly(monthly["id"]))
    changed = endpoint(client, "PATCH", f"{PLANS}/{monthly['id']}", editable(monthly, credits=2000))
    assert changed["credits"] == 2000
    assert (
        next(row for row in endpoint(client, "GET", PLANS) if row["id"] == annual["id"]) == annual
    )
    data = editable(annual)
    data.pop("monthly_plan_id")
    response = client.patch(f"/api/v1{PLANS}/{annual['id']}", json=data)
    assert response.status_code == 422, response.text
    fixed = endpoint(client, "PATCH", f"{PLANS}/{annual['id']}", {**data, "credits": 24000})
    assert fixed["monthly_plan_id"] == monthly["id"]


def test_comparison_link_never_changes_saved_price_or_membership_fulfillment(commerce):
    client, _, _, _, _ = commerce
    _, provider = setup_sales(client)
    monthly = endpoint(client, "POST", PLANS, payload())
    # A relationship does not force a discount or change the configured sale price.
    data = yearly(monthly["id"])
    data.update(amount_minor=31000, credits=15000)
    annual = endpoint(client, "POST", PLANS, data)
    order = membership_checkout(client, annual, provider)
    endpoint(
        client,
        "PATCH",
        f"{PLANS}/{monthly['id']}",
        editable(monthly, amount_minor=9900, credits=9999),
    )
    assert notify(client, provider, order).status_code == 204
    paid = endpoint(client, "GET", f"/billing/orders/{order['id']}")
    assert paid["status"] == "completed"
    assert (paid["amount_minor"], paid["credits"], paid["membership_days"]) == (31000, 15000, 360)
    assert paid["membership"]["credits"] == 15000
    assert (
        datetime.fromisoformat(paid["membership"]["expires_at"])
        - datetime.fromisoformat(paid["membership"]["starts_at"])
    ).days == 360


def test_link_changes_are_super_admin_only_and_have_database_identity_guards(commerce):
    client, sessions, _, _, other = commerce
    monthly = endpoint(client, "POST", PLANS, payload())
    annual = endpoint(client, "POST", PLANS, yearly(monthly["id"]))
    assert (
        client.post("/api/v1" + PLANS, json=yearly(monthly["id"]), headers=other).status_code == 403
    )
    assert (
        client.patch(
            f"/api/v1{PLANS}/{annual['id']}",
            json=editable(annual, monthly_plan_id=None),
            headers=other,
        ).status_code
        == 403
    )
    with sessions() as db:
        for invalid in (uuid.UUID(annual["id"]), uuid.uuid4()):
            with pytest.raises(IntegrityError), db.begin_nested():
                row = db.get(MembershipPlan, uuid.UUID(annual["id"]))
                row.monthly_plan_id = invalid
                db.flush()
        assert db.get(MembershipPlan, uuid.UUID(annual["id"])).monthly_plan_id == uuid.UUID(
            monthly["id"]
        )


def test_0067_preserves_existing_plan_terms_and_roundtrips(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0066_billing_memberships")
    plans = Table("membership_plan", MetaData(), schema=schema, autoload_with=conn)
    ident = uuid.uuid4()
    conn.execute(
        plans.insert().values(
            id=ident,
            name="Legacy plan",
            description="",
            features=[],
            currency="cny",
            amount_minor=1000,
            credits=6000,
            duration=12,
            duration_unit="month",
            sort_order=0,
            active=True,
        )
    )
    before = conn.execute(select(plans)).mappings().one()
    command.upgrade(config, "0067_membership_monthly_link")
    assert (
        conn.scalar(
            text(f'SELECT monthly_plan_id FROM "{schema}".membership_plan WHERE id=:id'),
            {"id": ident},
        )
        is None
    )
    assert conn.execute(select(plans)).mappings().one() == before
    assert "ix_membership_plan_monthly_plan_id" in {
        item["name"] for item in inspect(conn).get_indexes("membership_plan", schema=schema)
    }
    command.downgrade(config, "0066_billing_memberships")
    assert conn.execute(select(plans)).mappings().one() == before
    command.upgrade(config, "0067_membership_monthly_link")
