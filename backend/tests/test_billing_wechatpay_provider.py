"""Protocol tests use real RSA/AES-GCM and a local HTTP transport, never a merchant."""

import base64
import json
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.modules.billing import wechatpay_provider as wx
from app.modules.billing.providers import CheckoutRequest, ProviderError, provider_catalog

ORDER_ID = "8f444c90-54fa-4cba-b2e9-04e8d54d293e"
REFUND_ID = "637e468a-7681-4381-bf38-ea17e2f1bf21"
PAYMENT_ID = "420000000000000000000001"
REFUND_PROVIDER_ID = "503000000000000000000001"


@pytest.fixture(scope="module")
def keys():
    return tuple(rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))


@pytest.fixture
def config(keys):
    merchant, platform = keys
    return {
        "app_id": "wx1234567890abcdef",
        "mch_id": "1234567890",
        "merchant_serial": "ABC123",
        "merchant_private_key": merchant.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "api_v3_key": "0123456789abcdef0123456789abcdef",
        "platform_public_key_id": "PUB_KEY_ID_12345678901234567890",
        "platform_public_key": platform.public_key()
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
        success_url="https://studio.example/billing",
        cancel_url="https://studio.example/billing",
        webhook_url="https://studio.example/api/v1/billing/webhook/channel",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def encode(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()


def signed(raw, config, keys, *, timestamp=None):
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    nonce = "protocol-test-nonce"
    signature = keys[1].sign(
        timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw + b"\n",
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return {
        "Wechatpay-Serial": config["platform_public_key_id"],
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Signature": base64.b64encode(signature).decode(),
    }


def paid(config, **changes):
    return {
        "appid": config["app_id"],
        "mchid": config["mch_id"],
        "out_trade_no": UUID(ORDER_ID).hex,
        "transaction_id": PAYMENT_ID,
        "trade_type": "NATIVE",
        "trade_state": "SUCCESS",
        "amount": {"total": 1299, "payer_total": 1199, "currency": "CNY"},
        **changes,
    }


def notification(data, config, *, corrupt_cipher=False):
    nonce, associated = b"0123456789ab", b"transaction"
    ciphertext = AESGCM(config["api_v3_key"].encode()).encrypt(nonce, encode(data), associated)
    if corrupt_cipher:
        ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    return encode(
        {
            "id": "notification-test",
            "event_type": "TRANSACTION.SUCCESS",
            "resource_type": "encrypt-resource",
            "resource": {
                "original_type": "transaction",
                "algorithm": "AEAD_AES_256_GCM",
                "nonce": nonce.decode(),
                "associated_data": associated.decode(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
            },
        }
    )


def mock_transport(monkeypatch, config, keys, handler):
    real_client = httpx.Client

    def check(request):
        assert request.url.scheme == "https"
        assert request.url.host == "api.mch.weixin.qq.com"
        assert request.headers["Wechatpay-Serial"] == config["platform_public_key_id"]
        auth = request.headers["Authorization"]
        assert auth.startswith("WECHATPAY2-SHA256-RSA2048 ")
        fields = dict(re.findall(r'(\w+)="([^"]+)"', auth))
        assert fields["mchid"] == config["mch_id"]
        assert fields["serial_no"] == config["merchant_serial"]
        # Independent reconstruction checks both the query string and raw body.
        message = (
            request.method.encode()
            + b"\n"
            + request.url.raw_path
            + b"\n"
            + fields["timestamp"].encode()
            + b"\n"
            + fields["nonce_str"].encode()
            + b"\n"
            + request.content
            + b"\n"
        )
        keys[0].public_key().verify(
            base64.b64decode(fields["signature"]), message, padding.PKCS1v15(), hashes.SHA256()
        )
        return handler(request)

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == 5
        return real_client(transport=httpx.MockTransport(check), **kwargs)

    monkeypatch.setattr(wx.httpx, "Client", factory)


def response(data, config, keys, status=200):
    raw = encode(data)
    return httpx.Response(status, content=raw, headers=signed(raw, config, keys))


def test_catalog_and_config_reject_untrusted_gateway(config):
    from app.modules.billing.providers import get_provider, validate_provider_config

    assert {"stripe", "wechatpay"} <= {item["kind"] for item in provider_catalog()}
    assert isinstance(get_provider("wechatpay", config), wx.WeChatPayProvider)
    for kind, values in [("generic", config), ("wechatpay", {**config, "api_url": "https://bad"})]:
        with pytest.raises(ProviderError):
            validate_provider_config(kind, values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mch_id", '123456"injected'),
        ("app_id", "other-app"),
        ("api_v3_key", "short"),
        ("merchant_private_key", "invalid"),
        ("platform_public_key", "invalid"),
        ("platform_public_key_id", "unknown-certificate"),
    ],
)
def test_invalid_configuration(config, field, value):
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider({**config, field: value})


def test_native_checkout_has_exact_minor_units_stable_id_and_signed_request(
    monkeypatch, config, keys, order
):
    bodies = []

    def handler(request):
        assert request.url.path == "/v3/pay/transactions/native"
        body = json.loads(request.content)
        bodies.append(body)
        assert body["amount"] == {"total": 1299, "currency": "CNY"}
        assert body["out_trade_no"] == UUID(ORDER_ID).hex
        assert body["appid"] == config["app_id"]
        assert body["notify_url"] == order.webhook_url
        return response({"code_url": "weixin://wxpay/bizpayurl?pr=test"}, config, keys)

    mock_transport(monkeypatch, config, keys, handler)
    provider = wx.WeChatPayProvider(config)
    first = provider.create_checkout(order)
    assert first.qr_payload == "weixin://wxpay/bizpayurl?pr=test"
    assert first.checkout_url is None
    assert first.provider_order_id == UUID(ORDER_ID).hex
    provider.create_checkout(order)
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "usd"},
        {"amount_minor": 12.99},
        {"amount_minor": True},
        {"amount_minor": 0},
        {"order_id": "../other-order"},
        {"webhook_url": "http://studio.example/callback"},
        {"webhook_url": "https://user:secret@studio.example/callback"},
    ],
)
def test_bad_order_never_calls_network(monkeypatch, config, order, changes):
    monkeypatch.setattr(wx.httpx, "Client", lambda **kwargs: pytest.fail("unexpected request"))
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider(config).create_checkout(replace(order, **changes))


def test_signed_encrypted_notification_uses_total_not_discounted_payer_total(config, keys):
    raw = notification(paid(config), config)
    evidence = wx.WeChatPayProvider(config).verify_webhook(raw, signed(raw, config, keys))
    assert evidence.order_id == ORDER_ID
    assert evidence.state == "paid"
    assert evidence.payment_id == PAYMENT_ID
    assert evidence.amount_minor == 1299
    assert evidence.currency == "cny"


@pytest.mark.parametrize("mutation", ["body", "signature", "serial", "stale", "future", "nonce"])
def test_notification_signature_rejections(config, keys, mutation):
    raw = notification(paid(config), config)
    headers = signed(raw, config, keys)
    if mutation == "body":
        raw += b" "
    elif mutation == "signature":
        headers["Wechatpay-Signature"] = "WECHATPAY/SIGNTEST/fake"
    elif mutation == "serial":
        headers["Wechatpay-Serial"] = "PUB_KEY_ID_0000000000"
    elif mutation in {"stale", "future"}:
        delta = -301 if mutation == "stale" else 301
        headers = signed(raw, config, keys, timestamp=int(time.time()) + delta)
    else:
        headers["Wechatpay-Nonce"] = "injected\nvalue"
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider(config).verify_webhook(raw, headers)


def test_signed_but_tampered_ciphertext_fails(config, keys):
    raw = notification(paid(config), config, corrupt_cipher=True)
    with pytest.raises(ProviderError, match="解密"):
        wx.WeChatPayProvider(config).verify_webhook(raw, signed(raw, config, keys))


@pytest.mark.parametrize(
    "changes",
    [
        {"appid": "wx0000000000000000"},
        {"mchid": "9999999999"},
        {"trade_type": "JSAPI"},
        {"trade_state": "REFUND"},
        {"trade_state": []},
        {"trade_state": "NOTPAY"},
        {"transaction_id": None},
        {"amount": {"total": 1299, "currency": "USD"}},
        {"amount": {"total": 1299.0, "currency": "CNY"}},
        {"out_trade_no": "unrelated-order"},
    ],
)
def test_valid_signature_does_not_bypass_identity_or_semantic_checks(config, keys, changes):
    raw = notification(paid(config, **changes), config)
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider(config).verify_webhook(raw, signed(raw, config, keys))


def test_irrelevant_signed_event_is_ignored_only_after_verification(config, keys):
    raw = encode({"event_type": "REFUND.SUCCESS"})
    provider = wx.WeChatPayProvider(config)
    assert provider.verify_webhook(raw, signed(raw, config, keys)) is None
    with pytest.raises(ProviderError):
        provider.verify_webhook(raw, {})


@pytest.mark.parametrize(
    "state,expected", [("SUCCESS", "paid"), ("NOTPAY", "pending"), ("CLOSED", "expired")]
)
def test_query_is_signed_and_uses_original_identity(
    monkeypatch, config, keys, order, state, expected
):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith(UUID(ORDER_ID).hex)
        assert request.url.params["mchid"] == config["mch_id"]
        return response(paid(config, trade_state=state), config, keys)

    mock_transport(monkeypatch, config, keys, handler)
    evidence = wx.WeChatPayProvider(config).query_order(order, UUID(ORDER_ID).hex)
    assert evidence.state == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"out_trade_no": UUID(REFUND_ID).hex},
        {"amount": {"total": 1300, "currency": "CNY"}},
    ],
)
def test_query_rejects_authenticated_but_different_order(monkeypatch, config, keys, order, changes):
    mock_transport(
        monkeypatch, config, keys, lambda request: response(paid(config, **changes), config, keys)
    )
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider(config).query_order(order, None)


def refund_data(**changes):
    return {
        "out_trade_no": UUID(ORDER_ID).hex,
        "out_refund_no": UUID(REFUND_ID).hex,
        "transaction_id": PAYMENT_ID,
        "refund_id": REFUND_PROVIDER_ID,
        "status": "PROCESSING",
        "amount": {"total": 1299, "refund": 500, "currency": "CNY"},
        **changes,
    }


def test_refund_exact_minor_amount_stable_id_and_no_premature_success(
    monkeypatch, config, keys, order
):
    bodies = []

    def handler(request):
        if request.method == "GET":
            return response(paid(config), config, keys)
        assert request.method == "POST"
        data = json.loads(request.content)
        bodies.append(data)
        assert data["out_refund_no"] == UUID(REFUND_ID).hex
        assert data["transaction_id"] == PAYMENT_ID
        assert data["amount"] == {"refund": 500, "total": 1299, "currency": "CNY"}
        return response(refund_data(status="SUCCESS"), config, keys)

    mock_transport(monkeypatch, config, keys, handler)
    provider = wx.WeChatPayProvider(config)
    for _ in range(2):
        result = provider.create_refund(order, PAYMENT_ID, REFUND_ID, 500)
        assert result.state == "pending"
        assert result.provider_refund_id == REFUND_PROVIDER_ID
    assert bodies[0] == bodies[1]


def test_refund_cannot_move_money_for_another_payment(monkeypatch, config, keys, order):
    def handler(request):
        assert request.method == "GET", "mismatched payment must not reach refund POST"
        return response(paid(config, transaction_id="another-payment"), config, keys)

    mock_transport(monkeypatch, config, keys, handler)
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider(config).create_refund(order, PAYMENT_ID, REFUND_ID, 500)


@pytest.mark.parametrize(
    "state,expected",
    [
        ("SUCCESS", "succeeded"),
        ("CLOSED", "failed"),
        ("PROCESSING", "pending"),
        ("ABNORMAL", "pending"),
    ],
)
def test_refund_query_final_and_nonfinal_statuses(
    monkeypatch, config, keys, order, state, expected
):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith(UUID(REFUND_ID).hex)
        return response(refund_data(status=state), config, keys)

    mock_transport(monkeypatch, config, keys, handler)
    evidence = wx.WeChatPayProvider(config).query_refund(order, PAYMENT_ID, REFUND_ID, None)
    assert evidence.state == expected
    assert evidence.amount_minor == 500


@pytest.mark.parametrize(
    "changes",
    [
        {"out_refund_no": UUID(ORDER_ID).hex},
        {"transaction_id": "different-payment"},
        {"status": {}},
        {"out_trade_no": UUID(REFUND_ID).hex},
        {"refund_id": "different-refund"},
        {"amount": {"total": 1299, "refund": 1300, "currency": "CNY"}},
        {"amount": {"total": 1298, "refund": 500, "currency": "CNY"}},
    ],
)
def test_refund_query_rejects_wrong_identity_or_amount(monkeypatch, config, keys, order, changes):
    mock_transport(
        monkeypatch, config, keys, lambda request: response(refund_data(**changes), config, keys)
    )
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider(config).query_refund(order, PAYMENT_ID, REFUND_ID, REFUND_PROVIDER_ID)


@pytest.mark.parametrize("failure", ["unsigned", "http-error", "timeout", "malformed", "redirect"])
def test_uncertain_results_remain_errors_and_do_not_expose_provider_data(
    monkeypatch, config, keys, order, failure
):
    secret_body = "private-provider-detail"

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout(secret_body, request=request)
        if failure == "unsigned":
            return httpx.Response(200, json=paid(config))
        if failure == "malformed":
            raw = b"not-json"
            return httpx.Response(200, content=raw, headers=signed(raw, config, keys))
        return response(
            {"code": "SYSTEM_ERROR", "message": secret_body},
            config,
            keys,
            status=302 if failure == "redirect" else 500,
        )

    mock_transport(monkeypatch, config, keys, handler)
    with pytest.raises(ProviderError) as caught:
        wx.WeChatPayProvider(config).query_refund(order, PAYMENT_ID, REFUND_ID, None)
    assert secret_body not in str(caught.value)
    assert config["api_v3_key"] not in str(caught.value)


@pytest.mark.parametrize("failure", ["oversized", "compressed"])
def test_large_or_compressed_response_is_not_accepted(monkeypatch, config, keys, order, failure):
    def handler(request):
        if failure == "oversized":
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        import gzip

        return httpx.Response(
            200,
            content=gzip.compress(encode(paid(config))),
            headers={"Content-Encoding": "gzip"},
        )

    mock_transport(monkeypatch, config, keys, handler)
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider(config).query_order(order, None)


@pytest.mark.parametrize("outcome", ["expired", "paid", "pending", "unknown", "unsigned-204"])
def test_close_order_confirms_upstream_state(monkeypatch, config, keys, order, outcome):
    queries = 0

    def handler(request):
        nonlocal queries
        if request.method == "POST":
            assert (
                request.url.path == f"/v3/pay/transactions/out-trade-no/{UUID(ORDER_ID).hex}/close"
            )
            assert json.loads(request.content) == {"mchid": config["mch_id"]}
            if outcome == "expired":
                return httpx.Response(204, headers=signed(b"", config, keys))
            if outcome == "unsigned-204":
                return httpx.Response(204)
            raise httpx.ReadTimeout("close outcome unknown", request=request)
        queries += 1
        if queries > 1 and outcome == "unknown":
            raise httpx.ReadTimeout("query also unknown", request=request)
        state = "NOTPAY"
        if queries > 1:
            state = {"expired": "CLOSED", "unsigned-204": "CLOSED", "paid": "SUCCESS"}.get(
                outcome, "NOTPAY"
            )
        return response(paid(config, trade_state=state), config, keys)

    mock_transport(monkeypatch, config, keys, handler)
    provider = wx.WeChatPayProvider(config)
    if outcome == "unknown":
        with pytest.raises(ProviderError):
            provider.close_order(order, UUID(ORDER_ID).hex)
    else:
        expected = "expired" if outcome == "unsigned-204" else outcome
        assert provider.close_order(order, UUID(ORDER_ID).hex).state == expected


def test_close_order_rejects_wrong_provider_reference(monkeypatch, config, order):
    monkeypatch.setattr(
        wx.httpx, "Client", lambda **kwargs: pytest.fail("must not query wrong order")
    )
    with pytest.raises(ProviderError):
        wx.WeChatPayProvider(config).close_order(order, UUID(REFUND_ID).hex)


def test_close_order_does_not_close_paid_order(monkeypatch, config, keys, order):
    def handler(request):
        assert request.method == "GET"
        return response(paid(config), config, keys)

    mock_transport(monkeypatch, config, keys, handler)
    assert wx.WeChatPayProvider(config).close_order(order, UUID(ORDER_ID).hex).state == "paid"
