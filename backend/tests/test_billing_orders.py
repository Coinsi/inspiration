"""The billing API, state transitions, and ledger against isolated PostgreSQL."""

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.security import create_access_token
from app.models.billing import BillingOrder, BillingProvider, BillingRefund, CreditEntry
from app.models.generation import GenerationJob
from app.models.identity import User
from app.modules.billing import orders, wallet, worker
from app.modules.billing.providers import Checkout, PaymentEvidence, RefundEvidence
from tests.test_media_workflow import studio  # noqa: F401

API = "/api/v1"
EASYPAY_KEY = "fixture_only_easypay_key_2026"


class FakeProvider:
    def __init__(self):
        self.visible = {}
        self.created = []
        self.refunded = []
        self.refund_status = "pending"
        self.refund_timeout_once = False
        self.checkout_fails = False

    def create_checkout(self, order):
        self.created.append(order.order_id)
        if self.checkout_fails:
            raise ProviderError("timeout")
        return Checkout(
            "session-" + order.order_id,
            checkout_url="https://checkout.stripe.com/c/test",
        )

    def query_order(self, order, provider_order_id):
        return self.visible.get(
            order.order_id, PaymentEvidence(order.order_id, "pending", provider_order_id)
        )

    def close_order(self, order, provider_order_id):
        return self.visible.get(
            order.order_id, PaymentEvidence(order.order_id, "expired", provider_order_id)
        )

    def verify_webhook(self, raw, headers):
        if headers.get("x-fixture-signature") != "verified":
            raise ProviderError("invalid test signature")
        return PaymentEvidence(**json.loads(raw))

    def create_refund(self, order, payment_id, refund_id, amount_minor):
        value = (order.order_id, refund_id, amount_minor)
        self.refunded.append(value)
        if self.refund_timeout_once:
            self.refund_timeout_once = False
            raise ProviderError("Refund response lost after upstream accepted it")
        return RefundEvidence(self.refund_status, refund_id, amount_minor, order.currency)

    def query_refund(self, order, payment_id, refund_id, provider_refund_id):
        _, _, amount = next(item for item in self.refunded if item[1] == refund_id)
        return RefundEvidence(
            self.refund_status, provider_refund_id or refund_id, amount, order.currency
        )


def endpoint(client, method, path, payload=None, **kwargs):
    response = client.request(method, API + path, json=payload, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def commerce(studio, monkeypatch):  # noqa: F811
    client, sessions, ids = studio
    with sessions() as db:
        user = db.get(User, ids["user"])
        user.is_platform_admin = True
        user.platform_role = "super"
        other = User(
            username="billing_other",
            email="other@example.com",
            display_name="Other",
            is_platform_admin=True,
            platform_role="admin",
        )
        db.add(other)
        db.flush()
        other_token = create_access_token(str(other.id))
        db.commit()
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "credentials_fernet_key", key)
    monkeypatch.setattr(settings, "app_env", "test")
    provider = FakeProvider()
    monkeypatch.setattr(orders, "get_provider", lambda _kind, _config: provider)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    return client, sessions, ids, provider, {"Authorization": "Bearer " + other_token}


def setup_sales(client):
    pack = endpoint(
        client,
        "POST",
        "/admin/billing/packs",
        {
            "name": "Starter",
            "currency": "cny",
            "amount_minor": 103,
            "credits": 10001,
        },
    )
    provider = endpoint(
        client,
        "POST",
        "/admin/billing/providers",
        {
            "name": "Test Stripe",
            "kind": "stripe",
            "currency": "cny",
            "config": {"secret_key": "sk_test_fixture", "webhook_secret": "whsec_fixture"},
        },
    )
    endpoint(
        client,
        "PATCH",
        "/admin/billing/settings",
        {
            "enabled": True,
            "payment_required": False,
            "public_origin": "https://example.com",
            "checkout_minutes": 60,
        },
    )
    return pack, provider


def add_easypay(client):
    return endpoint(
        client,
        "POST",
        "/admin/billing/providers",
        {
            "name": "易支付（支付宝）",
            "kind": "easypay",
            "currency": "cny",
            "config": {
                "api_base": "https://merchant.example.com",
                "pid": "100001",
                "pkey": EASYPAY_KEY,
                "payment_type": "alipay",
                "payment_mode": "popup",
            },
        },
    )


def checkout(client, pack, provider, key=None):
    return endpoint(
        client,
        "POST",
        "/billing/orders",
        {
            "pack_id": pack["id"],
            "provider_id": provider["id"],
            "idempotency_key": str(key or uuid.uuid4()),
        },
    )


def notify(client, provider, order, **overrides):
    values = {
        "order_id": order["id"],
        "state": "paid",
        "provider_order_id": "session-" + order["id"],
        "payment_id": "intent-" + order["id"],
        "amount_minor": order["amount_minor"],
        "currency": order["currency"],
    }
    return client.post(
        f"{API}/billing/webhook/{provider['id']}",
        json={**values, **overrides},
        headers={"x-fixture-signature": "verified"},
    )


def read_wallet(client):
    return endpoint(client, "GET", "/billing/wallet")


def test_payment_defaults_permissions_and_encrypted_credentials(commerce):
    client, sessions, _, _, other = commerce
    assert endpoint(client, "GET", "/billing/catalog") == {
        "enabled": False,
        "packs": [],
        "providers": [],
    }
    assert read_wallet(client)["available"] == 0
    assert client.get(API + "/admin/billing/settings", headers=other).status_code == 403
    assert client.get(API + "/admin/billing/provider-types", headers=other).status_code == 403
    assert (
        client.post(
            API + "/admin/billing/providers",
            headers=other,
            json={
                "name": "Not allowed",
                "kind": "stripe",
                "currency": "usd",
                "config": {"secret_key": "sk_test_fixture", "webhook_secret": "whsec_fixture"},
            },
        ).status_code
        == 403
    )
    _, provider = setup_sales(client)
    assert "config" not in provider and "secret_key" not in provider
    with sessions() as db:
        row = db.get(BillingProvider, uuid.UUID(provider["id"]))
        assert b"sk_test_fixture" not in row.config_enc
        assert (
            json.loads(Fernet(settings.credentials_fernet_key.encode()).decrypt(row.config_enc))[
                "secret_key"
            ]
            == "sk_test_fixture"
        )


def test_order_price_snapshot_duplicate_callbacks_and_owner_boundaries(commerce):
    client, sessions, _, fake, other = commerce
    pack, provider = setup_sales(client)
    key = uuid.uuid4()
    order = checkout(client, pack, provider, key)
    assert read_wallet(client)["available"] == 0
    assert order["checkout_url"].startswith("https://checkout.stripe.com/")
    endpoint(
        client,
        "PATCH",
        "/admin/billing/packs/" + pack["id"],
        {
            "name": "Changed",
            "amount_minor": 999,
            "credits": 20000,
            "currency": "cny",
        },
    )
    again = checkout(client, pack, provider, key)
    assert again["id"] == order["id"]
    assert again["amount_minor"] == 103 and fake.created == [order["id"]]
    hidden = f"{API}/billing/orders/{order['id']}"
    assert client.get(hidden, headers=other).status_code == 404
    assert client.post(hidden + "/check", headers=other).status_code == 404
    assert notify(client, provider, order, amount_minor=102).status_code == 409
    assert notify(client, provider, order, currency="usd").status_code == 409
    assert read_wallet(client)["available"] == 0
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: notify(client, provider, order).status_code, range(2))
        )
    assert results == [204, 204]
    assert read_wallet(client)["available"] == 10001
    assert endpoint(client, "GET", f"/billing/orders/{order['id']}")["status"] == "completed"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(CreditEntry)) == 1


def test_reconciliation_recovers_without_callback(commerce):
    client, sessions, _, fake, _ = commerce
    pack, provider = setup_sales(client)
    key = uuid.uuid4()
    fake.checkout_fails = True
    pending = checkout(client, pack, provider, key)
    assert pending["status"] == "pending" and pending["checkout_url"] is None
    fake.checkout_fails = False
    order = checkout(client, pack, provider, key)
    assert order["id"] == pending["id"] and fake.created == [order["id"]] * 2
    fake.visible[order["id"]] = PaymentEvidence(
        order["id"],
        "paid",
        "session-" + order["id"],
        "intent-" + order["id"],
        order["amount_minor"],
        order["currency"],
    )
    with sessions() as db:
        db.get(BillingOrder, uuid.UUID(order["id"])).next_check_at = orders.now() - timedelta(
            seconds=1
        )
        db.commit()
    worker.reconcile_once()
    worker.reconcile_once()
    assert read_wallet(client)["available"] == 10001


def test_pending_refund_holds_points_and_partial_refunds_round_cumulatively(commerce):
    client, sessions, _, fake, _ = commerce
    pack, provider = setup_sales(client)
    order = checkout(client, pack, provider)
    assert notify(client, provider, order).status_code == 204
    path = f"/admin/billing/orders/{order['id']}/refunds"
    payload = {"amount_minor": 30, "reason": "partial", "idempotency_key": str(uuid.uuid4())}
    item = endpoint(client, "POST", path, payload)["refunds"][0]
    assert item["status"] == "pending"
    amount = 10001 * 30 // 103
    assert item["credits"] == amount
    assert read_wallet(client)["reserved"] == amount
    assert read_wallet(client)["available"] == 10001 - amount
    assert endpoint(client, "POST", path, payload)["refunds"][0]["id"] == item["id"]
    assert len(fake.refunded) == 1
    fake.refund_status = "succeeded"
    with sessions() as db:
        db.get(BillingRefund, uuid.UUID(item["id"])).next_check_at = orders.now()
        db.commit()
    worker.reconcile_once()
    assert read_wallet(client)["reserved"] == 0
    payload = {"amount_minor": 73, "reason": "rest", "idempotency_key": str(uuid.uuid4())}
    final = endpoint(client, "POST", path, payload)
    assert final["refunded_minor"] == 103
    assert final["refunds"][0]["credits"] == 10001 - amount
    assert read_wallet(client)["available"] == read_wallet(client)["reserved"] == 0
    assert len(fake.refunded) == 2


def test_failed_refund_releases_hold_and_spent_points_cannot_be_refunded(commerce):
    client, sessions, ids, fake, _ = commerce
    pack, provider = setup_sales(client)
    order = checkout(client, pack, provider)
    assert notify(client, provider, order).status_code == 204
    request_path = f"/billing/orders/{order['id']}/refund-request"
    refund = endpoint(client, "POST", request_path, {"reason": "return"})["refunds"][0]
    assert refund["status"] == "requested"
    fake.refund_status = "failed"
    result = endpoint(client, "POST", f"/admin/billing/refunds/{refund['id']}/approve", {})
    assert result["refunds"][0]["status"] == "failed"
    assert read_wallet(client)["available"] == 10001 and read_wallet(client)["reserved"] == 0
    with sessions() as db:
        wallet.change(db, ids["user"], "test_consumption", uuid.uuid4(), available=-1)
        db.commit()
    refund = endpoint(client, "POST", request_path, {"reason": "return"})["refunds"][0]
    response = client.post(f"{API}/admin/billing/refunds/{refund['id']}/approve", json={})
    assert response.status_code == 402
    assert read_wallet(client)["available"] == 10000 and read_wallet(client)["reserved"] == 0
    assert len(fake.refunded) == 1


def test_setup_callback_id_and_cancel_racing_with_payment(commerce):
    client, _, _, fake, other = commerce
    pack, _ = setup_sales(client)
    provider_id = str(uuid.uuid4())
    data = {
        "id": provider_id,
        "name": "Preconfigured Stripe",
        "kind": "stripe",
        "currency": "cny",
        "config": {"secret_key": "sk_test_fixture", "webhook_secret": "whsec_fixture"},
    }
    provider = endpoint(client, "POST", "/admin/billing/providers", data)
    assert provider["id"] == provider_id
    assert client.post(API + "/admin/billing/providers", json=data).status_code == 409
    order = checkout(client, pack, provider)
    cancel = f"/billing/orders/{order['id']}/cancel"
    assert client.post(API + cancel, headers=other).status_code == 404
    assert endpoint(client, "POST", cancel)["status"] == "cancelled"
    assert read_wallet(client)["available"] == 0
    next_order = checkout(client, pack, provider)
    oid = next_order["id"]
    fake.visible[oid] = PaymentEvidence(oid, "paid", "session-" + oid, "intent-" + oid, 103, "cny")
    assert endpoint(client, "POST", f"/billing/orders/{oid}/cancel")["status"] == "completed"
    assert read_wallet(client)["available"] == 10001


def test_reconciles_refund_when_first_response_is_lost(commerce):
    client, sessions, _, fake, _ = commerce
    pack, provider = setup_sales(client)
    order = checkout(client, pack, provider)
    assert notify(client, provider, order).status_code == 204
    fake.refund_timeout_once = True
    fake.refund_status = "succeeded"
    result = endpoint(
        client,
        "POST",
        f"/admin/billing/orders/{order['id']}/refunds",
        {
            "amount_minor": 103,
            "reason": "Timed out",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    refund = result["refunds"][0]
    assert refund["status"] == "pending"
    assert read_wallet(client)["reserved"] == 10001
    with sessions() as db:
        db.get(BillingRefund, uuid.UUID(refund["id"])).next_check_at = orders.now()
        db.commit()
    worker.reconcile_once()
    assert len(fake.refunded) == 1
    assert read_wallet(client)["reserved"] == 0
    result = endpoint(client, "GET", f"/billing/orders/{order['id']}")
    assert result["refunds"][0]["status"] == "succeeded" and result["refunded_minor"] == 103


def test_superadmin_can_only_release_terminal_holds_without_an_active_resume(commerce):
    client, sessions, ids, _, other = commerce

    def new_job(status, **snapshot):
        return GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            request_type="image",
            estimated_cost=5,
            status=status,
            created_by=ids["user"],
            params={},
            input_snapshot=snapshot,
        )

    with sessions() as db:
        job = new_job("canceled")
        db.add(job)
        db.flush()
        original = job.id
        wallet.change(db, ids["user"], "fixture_seed", uuid.uuid4(), available=1000)
        wallet.reserve_generation(db, ids["user"], job.id, 500)
        resume = new_job("running", billing={"reservation_job_id": str(original)})
        db.add(resume)
        db.flush()
        resumed = resume.id
        db.commit()
    assert read_wallet(client)["reserved"] == 500
    assert endpoint(client, "GET", "/admin/billing/reservations")[0]["job_id"] == str(original)
    path = "/admin/billing/reservations/" + str(original) + "/resolve"
    payload = {"action": "release", "reason": "Verified no upstream charge"}
    assert client.post(API + path, json=payload, headers=other).status_code == 403
    assert client.post(API + path, json=payload).status_code == 409
    with sessions() as db:
        db.get(GenerationJob, resumed).status = "failed"
        db.commit()
    assert endpoint(client, "POST", path, payload)["status"] == "released"
    assert endpoint(client, "POST", path, payload)["status"] == "released"
    assert read_wallet(client)["available"] == 1000
    assert read_wallet(client)["reserved"] == 0


def test_easypay_get_and_post_callback_require_live_query_before_credit(commerce, monkeypatch):
    from app.modules.billing import easypay_provider
    from app.modules.billing.providers import get_provider

    client, _, _, _, _ = commerce
    pack, stripe = setup_sales(client)
    provider = add_easypay(client)
    queried = []
    upstream_status = "0"

    def gateway(base_url, path, *, query=None, form=None):
        assert base_url == "https://merchant.example.com"
        assert path.strip("/") == "api.php" and query == form
        assert query["act"] == "order" and query["pid"] == "100001"
        assert query["key"] == EASYPAY_KEY
        queried.append(query["out_trade_no"])
        return {
            "code": 1,
            "status": upstream_status,
            "pid": "100001",
            "type": "alipay",
            "out_trade_no": query["out_trade_no"],
            "money": "1.03",
            "trade_no": "gateway-101",
        }

    monkeypatch.setattr(orders, "get_provider", get_provider)
    monkeypatch.setattr(easypay_provider, "request_json", gateway)
    order = checkout(client, pack, provider)
    assert order["checkout_url"].startswith("https://merchant.example.com/submit.php?")
    assert EASYPAY_KEY not in order["checkout_url"]
    assert not order["cancel_allowed"]
    form = {
        "pid": "100001",
        "type": "alipay",
        "out_trade_no": uuid.UUID(order["id"]).hex,
        "trade_no": "gateway-101",
        "money": "1.03",
        "trade_status": "TRADE_SUCCESS",
    }
    to_sign = "&".join(key + "=" + form[key] for key in sorted(form))
    form["sign"] = hashlib.md5((to_sign + EASYPAY_KEY).encode(), usedforsecurity=False).hexdigest()
    form["sign_type"] = "MD5"
    wire = urlencode(form)
    path = f"{API}/billing/webhook/{provider['id']}"
    assert client.get(f"{API}/billing/webhook/{stripe['id']}?" + wire).status_code == 405
    assert not queried
    assert client.get(path + "?" + wire).status_code >= 400
    assert read_wallet(client)["available"] == 0
    upstream_status = "1"
    callback = client.get(path + "?" + wire)
    assert callback.status_code == 200 and callback.text == "success"
    callback = client.post(
        path,
        content=wire,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert callback.status_code == 200 and callback.text == "success"
    assert queried == [form["out_trade_no"]] * 3
    assert read_wallet(client)["available"] == 10001


def test_easypay_refund_timeout_has_no_redelivery_and_manual_resolution_is_audited(
    commerce,
):
    client, sessions, ids, fake, other = commerce
    pack, _ = setup_sales(client)
    provider = add_easypay(client)
    order = checkout(client, pack, provider)
    assert notify(client, provider, order).text == "success"
    fake.refund_timeout_once = True
    path = f"/admin/billing/orders/{order['id']}/refunds"
    payload = {
        "amount_minor": 103,
        "reason": "退款测试",
        "idempotency_key": str(uuid.uuid4()),
    }
    original = endpoint(client, "POST", path, payload)
    refund = original["refunds"][0]
    assert refund["status"] == "pending" and refund["manual_resolution_allowed"]
    assert read_wallet(client)["reserved"] == 10001
    with sessions() as db:
        db.get(BillingRefund, uuid.UUID(refund["id"])).next_check_at = orders.now()
        db.commit()
    worker.reconcile_once()
    assert endpoint(client, "POST", path, payload)["refunds"][0]["status"] == "pending"
    assert (
        endpoint(client, "POST", f"/admin/billing/refunds/{refund['id']}/check")["refunds"][0][
            "status"
        ]
        == "pending"
    )
    assert len(fake.refunded) == 1
    resolution = f"/admin/billing/refunds/{refund['id']}/resolve"
    evidence = {"outcome": "succeeded", "reason": "商户后台确认已完成退款"}
    assert client.post(API + resolution, json=evidence, headers=other).status_code == 403
    assert endpoint(client, "POST", resolution, evidence)["refunded_minor"] == 103
    assert endpoint(client, "POST", resolution, evidence)["refunded_minor"] == 103
    assert client.post(API + resolution, json={**evidence, "outcome": "failed"}).status_code == 409
    assert read_wallet(client)["available"] == read_wallet(client)["reserved"] == 0
    from app.models.identity import AuditLog

    with sessions() as db:
        log = db.scalars(
            select(AuditLog).where(AuditLog.action == "billing.easypay_refund_resolved")
        ).one()
        assert log.user_id == ids["user"] and log.detail["reason"] == evidence["reason"]


def test_easypay_recorded_refund_success_does_not_require_a_fabricated_id(
    commerce,
    monkeypatch,
):
    client, sessions, _, fake, _ = commerce
    pack, _ = setup_sales(client)
    provider = add_easypay(client)
    order = checkout(client, pack, provider)
    assert notify(client, provider, order).text == "success"
    monkeypatch.setattr(
        fake,
        "create_refund",
        lambda order, _, refund, amount: RefundEvidence(
            "succeeded", amount_minor=amount, currency=order.currency
        ),
    )
    result = endpoint(
        client,
        "POST",
        f"/admin/billing/orders/{order['id']}/refunds",
        {
            "amount_minor": 30,
            "reason": "部分退款",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    refund = result["refunds"][0]
    assert refund["status"] == "succeeded" and not refund["manual_resolution_allowed"]
    with sessions() as db:
        assert db.get(BillingRefund, uuid.UUID(refund["id"])).provider_refund_id is None
    assert result["refunded_minor"] == 30 and read_wallet(client)["reserved"] == 0
