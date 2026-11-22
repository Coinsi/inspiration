"""Protocol-level adapter tests; no Stripe account or network requests."""

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from app.modules.billing import stripe_provider as stripe
from app.modules.billing.providers import CheckoutRequest, ProviderError

NOW = 1_800_000_000
ORDER_ID = "eec89fe0-8f1a-4dd7-93f4-9f633a609372"
REFUND_ID = "c14114e4-58be-445d-a472-9f86f4f9d92c"
CONFIG = {"secret_key": "sk_test_protocolfixture", "webhook_secret": "whsec_testfixture"}


@pytest.mark.parametrize("outcome", ["expired", "paid", "pending", "unknown"])
def test_close_order_confirms_expiry_or_recovers_unknown_result(
    provider, order, monkeypatch, outcome
):
    queries = 0

    def handle(request):
        nonlocal queries
        if request.method == "POST":
            assert request.url.path == "/v1/checkout/sessions/cs_test_fixture/expire"
            assert request.headers["idempotency-key"] == "inspiration-expire-" + ORDER_ID
            if outcome == "expired":
                return httpx.Response(
                    200,
                    json=session(status="expired", payment_status="unpaid", payment_intent=None),
                )
            raise httpx.ReadTimeout("unknown expiry", request=request)
        queries += 1
        if queries > 1 and outcome == "unknown":
            raise httpx.ReadTimeout("query also unknown", request=request)
        if queries > 1 and outcome == "paid":
            return httpx.Response(200, json=session())
        return httpx.Response(
            200, json=session(status="open", payment_status="unpaid", payment_intent=None)
        )

    mock_http(monkeypatch, handle)
    if outcome == "unknown":
        with pytest.raises(ProviderError):
            provider.close_order(order, "cs_test_fixture")
    else:
        assert provider.close_order(order, "cs_test_fixture").state == outcome


def test_close_order_validates_target_before_mutating(provider, order, monkeypatch):
    def handle(request):
        assert request.method == "GET", "must not expire an unrelated Checkout"
        return httpx.Response(
            200,
            json=session(
                id="cs_other", metadata={"order_id": REFUND_ID}, client_reference_id=REFUND_ID
            ),
        )

    mock_http(monkeypatch, handle)
    with pytest.raises(ProviderError):
        provider.close_order(order, "cs_other")


def test_close_order_leaves_already_paid_session_untouched(provider, order, monkeypatch):
    def handle(request):
        assert request.method == "GET"
        return httpx.Response(200, json=session())

    calls = mock_http(monkeypatch, handle)
    assert provider.close_order(order, "cs_test_fixture").state == "paid"
    assert len(calls) == 1


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(stripe.time, "time", lambda: NOW)
    return stripe.StripeProvider(CONFIG)


@pytest.fixture
def order():
    return CheckoutRequest(
        order_id=ORDER_ID,
        amount_minor=1234,
        currency="usd",
        title="Points",
        success_url="https://example.com/payment/success",
        cancel_url="https://example.com/payment/cancel",
        webhook_url="https://example.com/payment/webhook",
        expires_at=datetime.fromtimestamp(NOW + 3600, UTC),
    )


def session(**overrides):
    return {
        "id": "cs_test_fixture",
        "object": "checkout.session",
        "livemode": False,
        "metadata": {"order_id": ORDER_ID},
        "client_reference_id": ORDER_ID,
        "mode": "payment",
        "amount_total": 1234,
        "currency": "usd",
        "status": "complete",
        "payment_status": "paid",
        "payment_intent": "pi_fixture",
        "url": "https://checkout.stripe.com/c/pay/cs_test_fixture",
        **overrides,
    }


def payment(**overrides):
    return {
        "id": "pi_fixture",
        "object": "payment_intent",
        "livemode": False,
        "metadata": {"order_id": ORDER_ID},
        "status": "succeeded",
        "amount_received": 1234,
        "currency": "usd",
        **overrides,
    }


def refund(**overrides):
    return {
        "id": "re_fixture",
        "object": "refund",
        "payment_intent": "pi_fixture",
        "metadata": {"order_id": ORDER_ID, "refund_id": REFUND_ID},
        "status": "succeeded",
        "amount": 500,
        "currency": "usd",
        **overrides,
    }


def listing(items, more=False):
    return {"object": "list", "data": items, "has_more": more}


def mock_http(monkeypatch, handler):
    original = httpx.Client
    calls = []

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == 5

        def dispatch(request):
            calls.append(request)
            assert request.url.host == "api.stripe.com"
            assert request.url.scheme == "https"
            assert request.headers["stripe-version"] == "2026-11-18.endive"
            return handler(request)

        return original(**kwargs, transport=httpx.MockTransport(dispatch))

    monkeypatch.setattr(stripe.httpx, "Client", factory)
    return calls


def signed_event(obj=None, event_type="checkout.session.completed", timestamp=NOW, **event_fields):
    event = {
        "object": "event",
        "type": event_type,
        "livemode": False,
        "data": {"object": obj if obj is not None else session()},
        **event_fields,
    }
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
    digest = hmac.new(
        CONFIG["webhook_secret"].encode(),
        str(timestamp).encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return raw, {"Stripe-Signature": f"t={timestamp},v1={digest}"}


def test_checkout_exact_money_stable_idempotency_and_fixed_host(provider, order, monkeypatch):
    calls = mock_http(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json=session(status="open", payment_status="unpaid", payment_intent=None),
        ),
    )
    first = provider.create_checkout(order)
    provider.create_checkout(order)
    assert first.checkout_url.startswith("https://checkout.stripe.com/")
    assert calls[0].content == calls[1].content
    assert calls[0].headers["idempotency-key"] == "inspiration-checkout-" + ORDER_ID
    data = parse_qs(calls[0].content.decode())
    assert data["line_items[0][price_data][unit_amount]"] == ["1234"]
    assert data["metadata[order_id]"] == data["payment_intent_data[metadata][order_id]"]
    assert data["adaptive_pricing[enabled]"] == ["false"]
    assert data["expires_at"] == [str(NOW + 3600)]


@pytest.mark.parametrize(
    "change",
    [
        {"amount_minor": True},
        {"amount_minor": 1.25},
        {"amount_minor": 0},
        {"currency": "USD"},
        {"order_id": "untrusted"},
        {"success_url": "http://example.com"},
        {"cancel_url": "https://user:pass@example.com"},
        {"expires_at": datetime.fromtimestamp(NOW + 900, UTC)},
        {"expires_at": datetime.fromtimestamp(NOW + 90000, UTC)},
    ],
)
def test_checkout_rejects_invalid_input_without_http(provider, order, monkeypatch, change):
    calls = mock_http(monkeypatch, lambda req: pytest.fail("must not make a request"))
    with pytest.raises(ProviderError):
        provider.create_checkout(replace(order, **change))
    assert not calls


@pytest.mark.parametrize(
    "url",
    [
        "https://checkout.stripe.com.attacker.test/pay",
        "https://evil.test/pay",
        "https://checkout.stripe.com@evil.test/pay",
        "http://checkout.stripe.com/pay",
    ],
)
def test_checkout_rejects_untrusted_redirect(provider, order, monkeypatch, url):
    mock_http(monkeypatch, lambda req: httpx.Response(200, json=session(url=url)))
    with pytest.raises(ProviderError):
        provider.create_checkout(order)


def test_raw_body_signature_and_rotating_signature_match(provider):
    raw, headers = signed_event()
    headers["Stripe-Signature"] += ",v1=" + "0" * 64
    assert provider.verify_webhook(raw, headers).state == "paid"
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw + b" ", headers)


def test_fixed_hmac_protocol_vector(provider):
    # Frozen vector: HMAC-SHA256(secret, ASCII(timestamp) + '.' + exact body).
    # Unlike signed_event(), this expected signature is not computed by the test.
    raw = b'{"object":"event","type":"customer.created","livemode":false}'
    headers = {
        "stripe-signature": "t=1800000000,v1="
        "e2b093f6e330be07afbb1a0af0c5b1696575082605b06d7e35347dc88f020f41"
    }
    assert provider.verify_webhook(raw, headers) is None
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw.replace(b"false", b"true"), headers)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {**CONFIG, "secret_key": "pk_test_publickey"},
        {**CONFIG, "secret_key": "sk_test_secret\nheader"},
        {**CONFIG, "webhook_secret": "not_a_signing_secret"},
        {**CONFIG, "api_base": "https://attacker.test"},
    ],
)
def test_config_never_accepts_public_keys_or_gateway_override(config):
    with pytest.raises(ProviderError):
        stripe.StripeProvider(config)


@pytest.mark.parametrize("delta", [-301, 301])
def test_old_and_future_signed_events_rejected(provider, delta):
    raw, headers = signed_event(timestamp=NOW + delta)
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw, headers)


@pytest.mark.parametrize(
    "header",
    [
        "",
        "t=1800000000,v0=abc",
        "t=1800000000,t=1800000000,v1=abc",
        "t=notanumber,v1=abc",
        "t=1800000000,secret=whsec_testfixture,payload=abc",
    ],
)
def test_inappropriate_signature_fields_rejected(provider, header):
    raw, _ = signed_event()
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw, {"stripe-signature": header})


@pytest.mark.parametrize(
    "obj,state",
    [
        (session(payment_status="unpaid", payment_intent=None), "pending"),
        (session(payment_status="no_payment_required", payment_intent=None), "pending"),
        (session(status="expired", payment_status="unpaid", payment_intent=None), "expired"),
        (session(), "paid"),
    ],
)
def test_checkout_completion_is_not_proof_of_payment(provider, obj, state):
    raw, headers = signed_event(obj)
    assert provider.verify_webhook(raw, headers).state == state


@pytest.mark.parametrize(
    "change",
    [
        {"metadata": {"order_id": REFUND_ID}},
        {"client_reference_id": REFUND_ID},
        {"livemode": True},
        {"payment_intent": None},
        {"amount_total": 1.5},
        {"amount_total": True},
        {"mode": "subscription"},
        {"status": "open"},
    ],
)
def test_signed_invalid_payment_identity_is_rejected(provider, change):
    raw, headers = signed_event(session(**change))
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw, headers)


def test_connect_event_rejected_and_unrelated_event_ignored(provider):
    raw, headers = signed_event(account="acct_other")
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw, headers)
    raw, headers = signed_event(event_type="customer.created")
    assert provider.verify_webhook(raw, headers) is None


def test_query_order_rejects_other_session_or_amount(provider, order, monkeypatch):
    for change in ({"id": "cs_other"}, {"amount_total": 1235}, {"currency": "eur"}):
        monkeypatch.setattr(provider, "_request", lambda *a, change=change, **kw: session(**change))
        with pytest.raises(ProviderError):
            provider.query_order(order, "cs_test_fixture")


def test_lost_checkout_recovery_is_paginated_and_time_bound(provider, order, monkeypatch):
    def handler(req):
        assert req.url.params["created[gte]"] == str(NOW + 3600 - 86400)
        assert req.url.params["created[lte]"] == str(NOW + 3600)
        if "starting_after" not in req.url.params:
            return httpx.Response(
                200,
                json=listing(
                    [
                        session(
                            id="cs_other",
                            metadata={"order_id": REFUND_ID},
                        )
                    ],
                    more=True,
                ),
            )
        assert req.url.params["starting_after"] == "cs_other"
        return httpx.Response(200, json=listing([session()]))

    mock_http(monkeypatch, handler)
    assert provider.query_order(order, None).payment_id == "pi_fixture"


def test_lost_checkout_ambiguous_or_incomplete_is_not_paid(provider, order, monkeypatch):
    monkeypatch.setattr(provider, "_request", lambda *a, **kw: listing([session(), session()]))
    with pytest.raises(ProviderError):
        provider.query_order(order, None)
    monkeypatch.setattr(provider, "_request", lambda *a, **kw: listing([]))
    assert provider.query_order(order, None).state == "pending"
    monkeypatch.setattr(provider, "_request", lambda *a, **kw: listing([], more=True))
    with pytest.raises(ProviderError):
        provider.query_order(order, None)


def test_refund_retry_recovers_previous_refund_without_post(provider, order, monkeypatch):
    def handler(req):
        assert req.method == "GET"
        if req.url.path.startswith("/v1/payment_intents/"):
            return httpx.Response(200, json=payment())
        assert req.url.params["payment_intent"] == "pi_fixture"
        return httpx.Response(200, json=listing([refund()]))

    calls = mock_http(monkeypatch, handler)
    evidence = provider.create_refund(order, "pi_fixture", REFUND_ID, 500)
    assert evidence.state == "succeeded"
    assert len(calls) == 2


def test_refund_creation_exact_amount_idempotency_and_pending(provider, order, monkeypatch):
    def handler(req):
        if req.url.path.startswith("/v1/payment_intents/"):
            return httpx.Response(200, json=payment())
        if req.method == "GET":
            return httpx.Response(200, json=listing([]))
        assert req.headers["idempotency-key"] == "inspiration-refund-" + REFUND_ID
        data = parse_qs(req.content.decode())
        assert data["amount"] == ["500"]
        assert data["metadata[refund_id]"] == [REFUND_ID]
        return httpx.Response(200, json=refund(status="pending"))

    mock_http(monkeypatch, handler)
    assert provider.create_refund(order, "pi_fixture", REFUND_ID, 500).state == "pending"


@pytest.mark.parametrize(
    "change",
    [
        {"payment_intent": "pi_other"},
        {"metadata": {"order_id": ORDER_ID}},
        {"amount": 1500},
        {"currency": "eur"},
        {"id": "re_other"},
        {"status": "mystery"},
    ],
)
def test_refund_query_binds_identity_and_money(provider, order, monkeypatch, change):
    monkeypatch.setattr(
        provider,
        "_request",
        lambda method, path, **kw: payment() if "payment_intents" in path else refund(**change),
    )
    with pytest.raises(ProviderError):
        provider.query_refund(order, "pi_fixture", REFUND_ID, "re_fixture")


@pytest.mark.parametrize(
    "status,expected",
    [
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("canceled", "failed"),
        ("pending", "pending"),
        ("requires_action", "pending"),
    ],
)
def test_refund_terminal_states(provider, order, monkeypatch, status, expected):
    monkeypatch.setattr(
        provider,
        "_request",
        lambda method, path, **kw: (
            payment() if "payment_intents" in path else refund(status=status)
        ),
    )
    assert provider.query_refund(order, "pi_fixture", REFUND_ID, "re_fixture").state == expected


def test_missing_refund_is_uncertain_not_declined(provider, order, monkeypatch):
    monkeypatch.setattr(
        provider,
        "_request",
        lambda method, path, **kw: payment() if "payment_intents" in path else listing([]),
    )
    assert provider.query_refund(order, "pi_fixture", REFUND_ID, None).state == "pending"


@pytest.mark.parametrize("status", [302, 400, 401, 429, 500])
def test_http_error_does_not_leak_provider_body(provider, order, monkeypatch, status):
    mock_http(
        monkeypatch,
        lambda req: httpx.Response(
            status,
            json={
                "error": {"message": CONFIG["secret_key"] + " private customer information"},
            },
        ),
    )
    with pytest.raises(ProviderError) as exc:
        provider.query_order(order, "cs_test_fixture")
    assert CONFIG["secret_key"] not in str(exc.value)
    assert "private" not in str(exc.value)


def test_network_uncertainty_is_error_not_failed_refund(provider, order, monkeypatch):
    def handler(req):
        raise httpx.ReadTimeout("private data", request=req)

    mock_http(monkeypatch, handler)
    with pytest.raises(ProviderError) as exc:
        provider.query_refund(order, "pi_fixture", REFUND_ID, None)
    assert "private" not in str(exc.value)


@pytest.mark.parametrize(
    "change",
    [
        {"metadata": {"order_id": REFUND_ID}},
        {"amount_received": 1000},
        {"currency": "eur"},
        {"status": "processing"},
        {"livemode": True},
    ],
)
def test_refund_does_not_start_for_unmatched_payment(provider, order, monkeypatch, change):
    def handler(req):
        assert req.method == "GET" and req.url.path == "/v1/payment_intents/pi_fixture"
        return httpx.Response(200, json=payment(**change))

    calls = mock_http(monkeypatch, handler)
    with pytest.raises(ProviderError):
        provider.create_refund(order, "pi_fixture", REFUND_ID, 500)
    assert len(calls) == 1


@pytest.mark.parametrize("value", [[], {}, None, 42, True])
@pytest.mark.parametrize("field", ["status", "payment_status"])
def test_malformed_session_states_are_safe_errors(provider, order, monkeypatch, field, value):
    obj = session(**{field: value})
    raw, headers = signed_event(obj)
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw, headers)
    mock_http(monkeypatch, lambda req: httpx.Response(200, json=obj))
    with pytest.raises(ProviderError):
        provider.query_order(order, "cs_test_fixture")


@pytest.mark.parametrize("value", [[], {}, None, 42, True])
def test_malformed_event_type_is_safe_error(provider, value):
    raw, headers = signed_event(event_type=value)
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw, headers)


@pytest.mark.parametrize("value", [[], {}, None, 42, True])
def test_malformed_refund_status_is_safe_error(provider, order, monkeypatch, value):
    def handler(req):
        obj = payment() if "payment_intents" in req.url.path else refund(status=value)
        return httpx.Response(200, json=obj)

    mock_http(monkeypatch, handler)
    with pytest.raises(ProviderError):
        provider.query_refund(order, "pi_fixture", REFUND_ID, "re_fixture")


@pytest.mark.parametrize("declared_length", [False, True])
def test_oversized_response_is_closed_before_reading_all(
    provider, order, monkeypatch, declared_length
):
    class LargeResponse(httpx.SyncByteStream):
        chunks_read = 0
        closed = False

        def __iter__(self):
            for _ in range(100):
                self.chunks_read += 1
                yield b" " * 65536

        def close(self):
            self.closed = True

    stream = LargeResponse()
    headers = {"content-length": str(100 * 65536)} if declared_length else {}

    def handler(req):
        assert req.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers=headers, stream=stream)

    mock_http(monkeypatch, handler)
    with pytest.raises(ProviderError):
        provider.query_order(order, "cs_test_fixture")
    assert stream.closed
    assert stream.chunks_read <= (0 if declared_length else 17)


def test_compressed_response_is_rejected_before_decoding(provider, order, monkeypatch):
    mock_http(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=httpx.ByteStream(b"untrusted"),
        ),
    )
    with pytest.raises(ProviderError):
        provider.query_order(order, "cs_test_fixture")


def test_deeply_nested_json_in_api_or_signed_event_is_safe_error(provider, order, monkeypatch):
    raw = b'{"nested":' + b"[" * 10000 + b"0" + b"]" * 10000 + b"}"
    mock_http(monkeypatch, lambda req: httpx.Response(200, content=raw))
    with pytest.raises(ProviderError):
        provider.query_order(order, "cs_test_fixture")
    signature = hmac.new(
        CONFIG["webhook_secret"].encode(),
        str(NOW).encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw, {"stripe-signature": f"t={NOW},v1={signature}"})
