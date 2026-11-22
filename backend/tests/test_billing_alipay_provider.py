"""Official Alipay RSA2 wire protocol, using generated keys and mocked HTTP only."""

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.modules.billing import alipay_provider as ali
from app.modules.billing.providers import (
    CheckoutRequest,
    ProviderError,
    get_provider,
    provider_catalog,
)

ORDER_ID = "aa444c90-54fa-4cba-b2e9-04e8d54d293e"
REFUND_ID = "bb7e468a-7681-4381-bf38-ea17e2f1bf21"
PAYMENT_ID = "2026112222001001234567890123"
HEADERS = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}


@pytest.fixture(scope="module")
def keys():
    return tuple(rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))


@pytest.fixture
def config(keys):
    merchant, platform = keys
    return {
        "app_id": "2026112200000001",
        "seller_id": "2088123456789012",
        "app_private_key": merchant.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "alipay_public_key": platform.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode(),
    }


@pytest.fixture
def order():
    return CheckoutRequest(
        order_id=ORDER_ID,
        amount_minor=1299,
        currency="cny",
        title="积分充值",
        success_url="https://studio.example/billing?order=" + ORDER_ID,
        cancel_url="https://studio.example/billing",
        webhook_url="https://studio.example/notify",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def signature(raw, key):
    return base64.b64encode(key.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode()


def payment(**changes):
    return {
        "code": "10000",
        "out_trade_no": UUID(ORDER_ID).hex,
        "trade_no": PAYMENT_ID,
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "12.99",
        **changes,
    }


def callback(config, **changes):
    return {
        "app_id": config["app_id"],
        "seller_id": config["seller_id"],
        "auth_app_id": config["app_id"],
        "notify_type": "trade_status_sync",
        "notify_id": "notification-123",
        "notify_time": "2026-11-22 16:30:00",
        "charset": "utf-8",
        "out_trade_no": UUID(ORDER_ID).hex,
        "trade_no": PAYMENT_ID,
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "12.99",
        "subject": "积分充值",
        **changes,
    }


def signed_form(data, keys):
    raw = "&".join(f"{key}={data[key]}" for key in sorted(data) if data[key] != "").encode()
    return urlencode({**data, "sign_type": "RSA2", "sign": signature(raw, keys[1])}).encode()


def gateway_response(method, data, keys, *, pretty=False, signature_override=None):
    # Independently serialize with spaces/order/escaped slashes preserved to
    # catch implementations that reserialize instead of verifying wire bytes.
    node = json.dumps(data, ensure_ascii=False, indent=2 if pretty else None)
    sign = signature(node.encode(), keys[1]) if signature_override is None else signature_override
    body = '{ "sign": ' + json.dumps(sign) + ', "' + method.replace(".", "_")
    body += '_response" : ' + node + " }\n"
    return httpx.Response(200, content=body.encode(), headers={"Content-Type": "application/json"})


def mock_transport(monkeypatch, config, keys, handler):
    real_client = httpx.Client

    def verify_request(request):
        assert str(request.url) == "https://openapi.alipay.com/gateway.do"
        assert request.method == "POST"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        fields = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        signed = fields.pop("sign")
        canonical = "&".join(f"{key}={fields[key]}" for key in sorted(fields)).encode()
        keys[0].public_key().verify(
            base64.b64decode(signed), canonical, padding.PKCS1v15(), hashes.SHA256()
        )
        assert fields["app_id"] == config["app_id"]
        assert fields["sign_type"] == "RSA2"
        assert fields["charset"] == "utf-8"
        return handler(fields["method"], json.loads(fields["biz_content"]), request)

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == 5
        return real_client(transport=httpx.MockTransport(verify_request), **kwargs)

    monkeypatch.setattr(ali.httpx, "Client", factory)


def test_catalog_and_factory(config):
    catalog = next(item for item in provider_catalog() if item["kind"] == "alipay")
    assert {field["key"] for field in catalog["fields"]} == set(config)
    assert [field["key"] for field in catalog["fields"] if field["secret"]] == ["app_private_key"]
    assert isinstance(get_provider("alipay", config), ali.AlipayProvider)


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_id", "wrong"),
        ("seller_id", "another-merchant"),
        ("app_private_key", "invalid"),
        ("alipay_public_key", "invalid"),
        ("api_url", "https://untrusted.example"),
    ],
)
def test_configuration_rejected(config, field, value):
    with pytest.raises(ProviderError):
        get_provider("alipay", {**config, field: value})


def test_checkout_official_url_has_rsa2_and_exact_amount_expiry_and_stable_order(
    config, keys, order
):
    provider = ali.AlipayProvider(config)
    first = provider.create_checkout(order)
    assert first.provider_order_id == UUID(ORDER_ID).hex
    assert first.qr_payload is None
    parts = urlsplit(first.checkout_url)
    assert parts.scheme == "https" and parts.netloc == "openapi.alipay.com"
    assert parts.path == "/gateway.do"
    params = {key: values[0] for key, values in parse_qs(parts.query).items()}
    sign = params.pop("sign")
    canonical = "&".join(f"{key}={params[key]}" for key in sorted(params)).encode()
    keys[0].public_key().verify(
        base64.b64decode(sign), canonical, padding.PKCS1v15(), hashes.SHA256()
    )
    assert params["method"] == "alipay.trade.page.pay"
    assert params["sign_type"] == "RSA2"
    assert params["notify_url"] == order.webhook_url
    biz = json.loads(params["biz_content"])
    assert biz["total_amount"] == "12.99"
    assert biz["out_trade_no"] == UUID(ORDER_ID).hex
    assert biz["product_code"] == "FAST_INSTANT_TRADE_PAY"
    assert biz["time_expire"] == order.expires_at.astimezone(ali._CHINA).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    second = provider.create_checkout(order)
    second_params = parse_qs(urlsplit(second.checkout_url).query)
    assert second_params["biz_content"][0] == params["biz_content"]
    assert "PRIVATE" not in first.checkout_url


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "usd"},
        {"amount_minor": 12.99},
        {"amount_minor": True},
        {"amount_minor": 0},
        {"order_id": "../other"},
        {"success_url": "http://studio.example"},
        {"webhook_url": "https://secret@studio.example"},
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
    ],
)
def test_invalid_checkout(config, order, changes):
    with pytest.raises(ProviderError):
        ali.AlipayProvider(config).create_checkout(replace(order, **changes))


def test_title_sanitized_deterministically_for_official_page_api(config, order):
    result = ali.AlipayProvider(config).create_checkout(replace(order, title="a/b=c&d"))
    biz = json.loads(parse_qs(urlsplit(result.checkout_url).query)["biz_content"][0])
    assert biz["subject"] == "a b c d"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("TRADE_SUCCESS", "paid"),
        ("TRADE_FINISHED", "paid"),
        ("WAIT_BUYER_PAY", "pending"),
        ("TRADE_CLOSED", "expired"),
    ],
)
def test_form_notification_real_rsa2(config, keys, state, expected):
    evidence = ali.AlipayProvider(config).verify_webhook(
        signed_form(callback(config, trade_status=state), keys), HEADERS
    )
    assert evidence.order_id == ORDER_ID
    assert evidence.state == expected
    assert evidence.amount_minor == 1299
    assert evidence.currency == "cny"
    assert evidence.payment_id == PAYMENT_ID


@pytest.mark.parametrize(
    "changes",
    [
        {"app_id": "2026112200000099"},
        {"seller_id": "2088123456789099"},
        {"auth_app_id": "2026112200000099"},
        {"total_amount": "NaN"},
        {"total_amount": "12.999"},
        {"total_amount": "1e2"},
        {"total_amount": "-1.00"},
        {"total_amount": "0.00"},
        {"currency": "USD"},
        {"trans_currency": "USD"},
        {"trade_status": "UNKNOWN"},
        {"out_trade_no": "wrong-order"},
        {"trade_no": "../other"},
        {"notify_type": "another-event"},
    ],
)
def test_authentic_callback_still_requires_identity_and_valid_money(config, keys, changes):
    with pytest.raises(ProviderError):
        ali.AlipayProvider(config).verify_webhook(
            signed_form(callback(config, **changes), keys), HEADERS
        )


@pytest.mark.parametrize(
    "change", ["signature", "algorithm", "body", "duplicate", "encoding", "content-type"]
)
def test_callback_protocol_failures(config, keys, change):
    raw = signed_form(callback(config), keys)
    headers = HEADERS
    if change == "signature":
        fields = {key: values[0] for key, values in parse_qs(raw.decode()).items()}
        fields["sign"] = "invalid"
        raw = urlencode(fields).encode()
    elif change == "algorithm":
        raw = raw.replace(b"sign_type=RSA2", b"sign_type=RSA")
    elif change == "body":
        raw = raw.replace(b"total_amount=12.99", b"total_amount=99.99")
    elif change == "duplicate":
        raw += b"&total_amount=12.99"
    elif change == "encoding":
        raw += b"&bad=%zz"
    else:
        headers = {"Content-Type": "application/json"}
    with pytest.raises(ProviderError):
        ali.AlipayProvider(config).verify_webhook(raw, headers)


def test_callback_decodes_once_and_excludes_empty_values(config, keys):
    data = callback(config, subject="中文 + %2B", optional="")
    evidence = ali.AlipayProvider(config).verify_webhook(signed_form(data, keys), HEADERS)
    assert evidence.state == "paid"


@pytest.mark.parametrize("pretty", [False, True])
def test_query_verifies_original_json_node_and_identity(monkeypatch, config, keys, order, pretty):
    def handle(method, biz, request):
        assert method == "alipay.trade.query"
        assert biz == {"out_trade_no": UUID(ORDER_ID).hex}
        return gateway_response(
            method, payment(extra='包含 "sign": / { } 内容'), keys, pretty=pretty
        )

    mock_transport(monkeypatch, config, keys, handle)
    evidence = ali.AlipayProvider(config).query_order(order, UUID(ORDER_ID).hex)
    assert evidence.state == "paid"
    assert evidence.amount_minor == 1299


@pytest.mark.parametrize(
    "changes",
    [
        {"out_trade_no": UUID(REFUND_ID).hex},
        {"total_amount": "12.98"},
        {"trade_status": []},
        {"app_id": "2026112200000099"},
        {"seller_id": "2088123456789099"},
        {"currency": "USD"},
    ],
)
def test_query_rejects_wrong_identity(monkeypatch, config, keys, order, changes):
    mock_transport(
        monkeypatch,
        config,
        keys,
        lambda method, biz, request: gateway_response(method, payment(**changes), keys),
    )
    with pytest.raises(ProviderError):
        ali.AlipayProvider(config).query_order(order, None)


@pytest.mark.parametrize(
    "fault", ["missing-signature", "bad-signature", "tampered", "wrong-response", "duplicate"]
)
def test_gateway_signature_is_required(monkeypatch, config, keys, order, fault):
    def handle(method, biz, request):
        if fault == "missing-signature":
            return httpx.Response(200, json={"alipay_trade_query_response": payment()})
        if fault == "bad-signature":
            return gateway_response(method, payment(), keys, signature_override="not-signature")
        if fault == "wrong-response":
            return gateway_response("alipay.trade.refund", payment(), keys)
        response = gateway_response(method, payment(), keys)
        raw = response.content
        if fault == "tampered":
            raw = raw.replace(b"12.99", b"19.99")
        else:
            raw = raw.replace(
                b'"total_amount": "12.99"', b'"total_amount": "12.99", "total_amount": "12.99"'
            )
        return httpx.Response(200, content=raw)

    mock_transport(monkeypatch, config, keys, handle)
    with pytest.raises(ProviderError):
        ali.AlipayProvider(config).query_order(order, None)


def refund(**changes):
    return {
        "code": "10000",
        "out_trade_no": UUID(ORDER_ID).hex,
        "trade_no": PAYMENT_ID,
        "out_request_no": UUID(REFUND_ID).hex,
        "total_amount": "12.99",
        "refund_amount": "5.00",
        "refund_status": "REFUND_SUCCESS",
        **changes,
    }


def test_refund_uses_exact_stable_reference_and_waits_for_query(monkeypatch, config, keys, order):
    requests = []

    def handle(method, biz, request):
        if method == "alipay.trade.query":
            return gateway_response(method, payment(), keys)
        assert method == "alipay.trade.refund"
        requests.append(biz)
        assert biz == {
            "out_trade_no": UUID(ORDER_ID).hex,
            "out_request_no": UUID(REFUND_ID).hex,
            "refund_amount": "5.00",
        }
        # Cumulative refund_fee intentionally differs from this partial refund.
        return gateway_response(
            method,
            {
                "code": "10000",
                "out_trade_no": UUID(ORDER_ID).hex,
                "trade_no": PAYMENT_ID,
                "fund_change": "Y",
                "refund_fee": "7.00",
            },
            keys,
        )

    mock_transport(monkeypatch, config, keys, handle)
    provider = ali.AlipayProvider(config)
    for _ in range(2):
        evidence = provider.create_refund(order, PAYMENT_ID, REFUND_ID, 500)
        assert evidence.state == "pending"
        assert evidence.provider_refund_id == UUID(REFUND_ID).hex
        assert evidence.amount_minor == 500
    assert requests[0] == requests[1]


def test_wrong_payment_id_prevents_refund_request(monkeypatch, config, keys, order):
    def handle(method, biz, request):
        assert method == "alipay.trade.query", "refund must not be submitted"
        return gateway_response(method, payment(), keys)

    mock_transport(monkeypatch, config, keys, handle)
    with pytest.raises(ProviderError):
        ali.AlipayProvider(config).create_refund(
            order, "2026112222001001234567890199", REFUND_ID, 500
        )


def test_refund_query_requires_explicit_success(monkeypatch, config, keys, order):
    mock_transport(
        monkeypatch,
        config,
        keys,
        lambda method, biz, request: gateway_response(method, refund(), keys),
    )
    evidence = ali.AlipayProvider(config).query_refund(order, PAYMENT_ID, REFUND_ID, None)
    assert evidence.state == "succeeded"
    assert evidence.amount_minor == 500
    assert evidence.currency == "cny"


@pytest.mark.parametrize(
    "data", [{"code": "10000"}, refund(refund_status=None), refund(refund_status="")]
)
def test_missing_refund_status_is_pending_not_failed(monkeypatch, config, keys, order, data):
    mock_transport(
        monkeypatch, config, keys, lambda method, biz, request: gateway_response(method, data, keys)
    )
    assert (
        ali.AlipayProvider(config).query_refund(order, PAYMENT_ID, REFUND_ID, None).state
        == "pending"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"out_trade_no": UUID(REFUND_ID).hex},
        {"out_request_no": UUID(ORDER_ID).hex},
        {"trade_no": "2026112222001001234567890199"},
        {"total_amount": "12.98"},
        {"refund_amount": "13.00"},
        {"refund_amount": "5.001"},
        {"refund_status": []},
        {"refund_status": "FAILED"},
    ],
)
def test_refund_query_mismatch_or_unknown_status_not_success(
    monkeypatch, config, keys, order, changes
):
    mock_transport(
        monkeypatch,
        config,
        keys,
        lambda method, biz, request: gateway_response(method, refund(**changes), keys),
    )
    with pytest.raises(ProviderError):
        ali.AlipayProvider(config).query_refund(order, PAYMENT_ID, REFUND_ID, None)


@pytest.mark.parametrize(
    "failure",
    ["timeout", "http-error", "redirect", "unsigned", "not-found", "system-error", "oversized"],
)
def test_uncertainty_never_means_refund_failed_and_errors_are_safe(
    monkeypatch, config, keys, order, failure
):
    secret = "sensitive-provider-information"

    def handle(method, biz, request):
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        if failure in ("http-error", "redirect"):
            return httpx.Response(500 if failure == "http-error" else 302, text=secret)
        if failure == "oversized":
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        if failure == "unsigned":
            return httpx.Response(200, text=secret)
        return gateway_response(
            method,
            {
                "code": "40004",
                "sub_code": "ACQ.TRADE_NOT_EXIST" if failure == "not-found" else "ACQ.SYSTEM_ERROR",
                "sub_msg": secret,
            },
            keys,
        )

    mock_transport(monkeypatch, config, keys, handle)
    with pytest.raises(ProviderError) as caught:
        ali.AlipayProvider(config).query_refund(order, PAYMENT_ID, REFUND_ID, None)
    assert secret not in str(caught.value)
    assert "PRIVATE KEY" not in str(caught.value)


@pytest.mark.parametrize("outcome", ["expired", "paid", "pending", "unknown"])
def test_close_order_uses_confirmed_state_after_close_or_timeout(
    monkeypatch, config, keys, order, outcome
):
    queries = 0

    def handle(method, biz, request):
        nonlocal queries
        assert biz == {"out_trade_no": UUID(ORDER_ID).hex}
        if method == "alipay.trade.close":
            if outcome == "expired":
                return gateway_response(
                    method,
                    {"code": "10000", "out_trade_no": UUID(ORDER_ID).hex, "trade_no": PAYMENT_ID},
                    keys,
                )
            raise httpx.ReadTimeout("unknown close", request=request)
        assert method == "alipay.trade.query"
        queries += 1
        if queries > 1 and outcome == "unknown":
            raise httpx.ReadTimeout("query also unknown", request=request)
        state = "WAIT_BUYER_PAY"
        if queries > 1:
            state = {"expired": "TRADE_CLOSED", "paid": "TRADE_SUCCESS"}.get(outcome, state)
        return gateway_response(method, payment(trade_status=state), keys)

    mock_transport(monkeypatch, config, keys, handle)
    provider = ali.AlipayProvider(config)
    if outcome == "unknown":
        with pytest.raises(ProviderError):
            provider.close_order(order, UUID(ORDER_ID).hex)
    else:
        assert provider.close_order(order, UUID(ORDER_ID).hex).state == outcome


def test_close_order_provider_mismatch_never_calls_gateway(monkeypatch, config, order):
    monkeypatch.setattr(
        ali.httpx, "Client", lambda **kwargs: pytest.fail("must not query wrong order")
    )
    with pytest.raises(ProviderError):
        ali.AlipayProvider(config).close_order(order, UUID(REFUND_ID).hex)


def test_close_order_does_not_close_already_paid_trade(monkeypatch, config, keys, order):
    def handle(method, biz, request):
        assert method == "alipay.trade.query"
        return gateway_response(method, payment(), keys)

    mock_transport(monkeypatch, config, keys, handle)
    assert ali.AlipayProvider(config).close_order(order, UUID(ORDER_ID).hex).state == "paid"
