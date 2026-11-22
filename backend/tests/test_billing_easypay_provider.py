"""EasyPay V1 protocol fixtures; all gateway interactions are replaced locally."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import pytest

from app.modules.billing import easypay_provider as ep
from app.modules.billing.providers import CheckoutRequest, ProviderError

ORDER_ID = "da444c90-54fa-4cba-b2e9-04e8d54d293e"
REFUND_ID = "db7e468a-7681-4381-bf38-ea17e2f1bf21"
PAYMENT_ID = "20261122123456"
HEADERS = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}


@pytest.fixture
def config():
    return {
        "api_base": "https://gateway.example.com/pay/submit.php",
        "pid": "1001",
        "pkey": "test_secret_key",
        "payment_type": "alipay",
        "payment_mode": "qrcode",
    }


@pytest.fixture
def order():
    return CheckoutRequest(
        order_id=ORDER_ID,
        amount_minor=1299,
        currency="cny",
        title="积分充值",
        success_url="https://studio.example.com/billing?order=" + ORDER_ID,
        cancel_url="https://studio.example.com/billing",
        webhook_url="https://studio.example.com/notify",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        client_ip="203.0.113.9",
        is_mobile=False,
    )


def independent_sign(data, key="test_secret_key"):
    text = (
        "&".join(
            f"{name}={data[name]}"
            for name in sorted(data)
            if name not in ("sign", "sign_type") and data[name] != ""
        )
        + key
    )
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


def paid(**changes):
    return {
        "code": 1,
        "pid": "1001",
        "out_trade_no": UUID(ORDER_ID).hex,
        "status": 1,
        "money": "12.99",
        "trade_no": PAYMENT_ID,
        "type": "alipay",
        **changes,
    }


def notification(**changes):
    data = {
        "pid": "1001",
        "out_trade_no": UUID(ORDER_ID).hex,
        "trade_no": PAYMENT_ID,
        "trade_status": "TRADE_SUCCESS",
        "type": "alipay",
        "money": "12.99",
        "name": "积分充值",
        **changes,
    }
    data["sign"] = independent_sign(data)
    data["sign_type"] = "MD5"
    return urlencode(data).encode()


def mock_gateway(monkeypatch, callback):
    calls = []

    def request(base_url, endpoint, *, query=None, form=None):
        assert base_url == "https://gateway.example.com/pay"
        calls.append((endpoint, query, form))
        return callback(endpoint, query, form)

    monkeypatch.setattr(ep, "request_json", request)
    return calls


def test_fixed_sub2api_v1_md5_protocol_vector():
    params = {
        "pid": "1001",
        "type": "alipay",
        "out_trade_no": "ORDER123",
        "name": "Test Product",
        "money": "10.00",
        "ignored": "",
        "sign": "excluded",
        "sign_type": "MD5",
    }
    assert ep._sign(params, "test_secret_key") == "4a5e4a489ee20c0ff8ef42be7c9effee"


@pytest.mark.parametrize(
    "change",
    [
        {"pid": "1001&key=other"},
        {"pkey": "secret\nheader"},
        {"payment_type": "card"},
        {"payment_mode": "unknown"},
        {"refund_success_code": "2"},
        {"channel_id": "../bad"},
        {"extra": "value"},
        {"api_base": "http://gateway.example.com"},
        {"api_base": "https://127.0.0.1"},
        {"api_base": "https://user:pass@gateway.example.com"},
    ],
)
def test_invalid_config_is_rejected(config, change):
    with pytest.raises(ProviderError):
        ep.EasyPayProvider({**config, **change})


def test_popup_is_local_signed_url_without_exposing_key(monkeypatch, config, order):
    mock_gateway(monkeypatch, lambda *args: pytest.fail("popup must not contact gateway"))
    provider = ep.EasyPayProvider({**config, "payment_mode": "popup", "channel_id": "19"})
    first = provider.create_checkout(replace(order, is_mobile=True, client_ip=None))
    second = provider.create_checkout(replace(order, is_mobile=True, client_ip=None))
    assert first == second
    assert first.provider_order_id == UUID(ORDER_ID).hex
    fields = {
        key: values[0] for key, values in parse_qs(urlsplit(first.checkout_url).query).items()
    }
    assert fields["pid"] == "1001"
    assert fields["cid"] == "19"
    assert fields["device"] == "mobile"
    assert fields["money"] == "12.99"
    assert fields["sign"] == independent_sign(fields)
    assert "key" not in fields and "pkey" not in fields
    assert config["pkey"] not in first.checkout_url
    assert "clientip" not in fields
    assert first.checkout_url.startswith("https://gateway.example.com/pay/submit.php?")


@pytest.mark.parametrize(
    "mobile,expected",
    [
        (False, "https://gateway.example.com/pay/desktop"),
        (True, "https://gateway.example.com/mobile"),
    ],
)
def test_mapi_exact_integer_amount_and_mobile_payurl_selection(
    monkeypatch, config, order, mobile, expected
):
    def gateway(endpoint, query, form):
        assert endpoint == "mapi.php" and query is None
        assert form["clientip"] == "203.0.113.9"
        assert form["money"] == "12.99"
        assert form["out_trade_no"] == UUID(ORDER_ID).hex
        assert form["sign"] == independent_sign(form)
        assert (form.get("device") == "mobile") == mobile
        return {
            "code": "1",
            "trade_no": PAYMENT_ID,
            "payurl": "/pay/desktop",
            "payurl2": "/mobile",
            "qrcode": "weixin://wxpay/bizpayurl?pr=fixture",
        }

    mock_gateway(monkeypatch, gateway)
    result = ep.EasyPayProvider(config).create_checkout(replace(order, is_mobile=mobile))
    assert result.checkout_url == expected
    assert result.provider_order_id == UUID(ORDER_ID).hex
    assert result.qr_payload == "weixin://wxpay/bizpayurl?pr=fixture"


def test_mapi_mobile_deep_link_is_only_qr_data(monkeypatch, config, order):
    mock_gateway(
        monkeypatch, lambda *args: {"code": 1, "payurl2": "alipays://platformapi/startapp?appId=1"}
    )
    result = ep.EasyPayProvider(config).create_checkout(replace(order, is_mobile=True))
    assert result.checkout_url is None
    assert result.qr_payload.startswith("alipays://")


@pytest.mark.parametrize(
    "change",
    [
        {"currency": "usd"},
        {"amount_minor": 12.99},
        {"amount_minor": True},
        {"amount_minor": 0},
        {"order_id": "untrusted"},
        {"client_ip": None},
        {"client_ip": "1.2.3.4&pid=9"},
        {"webhook_url": "https://user:secret@studio.example.com"},
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
    ],
)
def test_invalid_checkout_never_dispatches(monkeypatch, config, order, change):
    mock_gateway(monkeypatch, lambda *args: pytest.fail("must not call gateway"))
    with pytest.raises(ProviderError):
        ep.EasyPayProvider(config).create_checkout(replace(order, **change))


@pytest.mark.parametrize(
    "result",
    [
        {"code": 0, "msg": "secret-error"},
        {"code": True, "payurl": "https://gateway.example.com/pay"},
        {"code": 1, "payurl": "javascript:alert(1)"},
        {"code": 1, "payurl": "http://gateway.example.com/pay"},
        {"code": 1, "payurl": "https://127.0.0.1/pay"},
        {"code": 1, "payurl": "https://127.1/pay"},
        {"code": 1, "payurl": "https://0177.0.0.1/pay"},
        {"code": 1, "payurl": "https://0x7f.0.0.1/pay"},
        {"code": 1, "payurl": "https://@gateway.example.com/pay"},
        {"code": 1, "payurl": "https://service.localhost/pay"},
        {"code": 1, "payurl": "https://[broken/pay"},
        {"code": 1, "qrcode": "weixin:invalid-payload"},
        {"code": 1, "payurl": "//attacker.example.com/pay"},
        {"code": 1, "payurl": "https://user:pass@gateway.example.com"},
        {"code": 1, "payurl": "https://gateway.example.com/pay?key=test_secret_key"},
        {"code": 1, "out_trade_no": UUID(REFUND_ID).hex, "qrcode": "opaque-token"},
        {"code": 1},
    ],
)
def test_unsafe_or_unconfirmed_checkout_response(monkeypatch, config, order, result):
    mock_gateway(monkeypatch, lambda *args: result)
    with pytest.raises(ProviderError) as caught:
        ep.EasyPayProvider(config).create_checkout(order)
    assert "secret-error" not in str(caught.value)


@pytest.mark.parametrize(
    "result",
    [
        paid(),
        paid(code="1", status="1", trade_no=int(PAYMENT_ID)),
        paid(money=Decimal("12.99")),
        {"code": "1", "data": {"status": "1", "money": "12.99", "trade_no": PAYMENT_ID}},
        {"data": {"trade_status": "TRADE_SUCCESS", "money": "12.99", "trade_no": PAYMENT_ID}},
    ],
)
def test_query_supports_php_scalars_and_nested_data_with_credentials_server_side(
    monkeypatch, config, order, result
):
    def gateway(endpoint, query, form):
        assert endpoint == "api.php"
        assert (
            query
            == form
            == {
                "act": "order",
                "pid": "1001",
                "key": "test_secret_key",
                "out_trade_no": UUID(ORDER_ID).hex,
            }
        )
        return result

    mock_gateway(monkeypatch, gateway)
    evidence = ep.EasyPayProvider(config).query_order(order, UUID(ORDER_ID).hex)
    assert evidence.state == "paid" and evidence.amount_minor == 1299
    assert evidence.payment_id == PAYMENT_ID
    assert evidence.provider_order_id == UUID(ORDER_ID).hex
    assert evidence.order_id == ORDER_ID


@pytest.mark.parametrize(
    "result",
    [
        paid(code=0),
        paid(code=True),
        paid(status=True),
        paid(money=12.99),
        paid(money="12.999"),
        paid(money="NaN"),
        paid(money="12.98"),
        paid(out_trade_no=UUID(REFUND_ID).hex),
        paid(pid="1002"),
        paid(type="wxpay"),
        paid(trade_no=None),
        paid(money=None),
        paid(data={"status": "0"}),
        paid(trade_status="WAIT_BUYER_PAY"),
        {"code": 1},
    ],
)
def test_query_rejects_ambiguous_or_wrong_order_data(monkeypatch, config, order, result):
    mock_gateway(monkeypatch, lambda *args: result)
    with pytest.raises(ProviderError):
        ep.EasyPayProvider(config).query_order(order, None)


def test_callback_never_credits_without_remote_confirmation(monkeypatch, config):
    calls = mock_gateway(monkeypatch, lambda *args: paid())
    evidence = ep.EasyPayProvider(config).verify_webhook(notification(), HEADERS)
    assert evidence.state == "paid" and len(calls) == 1
    assert calls[0][1]["out_trade_no"] == UUID(ORDER_ID).hex


def test_get_callback_and_name_delimiters_are_supported_with_remote_confirmation(
    monkeypatch, config
):
    mock_gateway(monkeypatch, lambda *args: paid())
    assert (
        ep.EasyPayProvider(config).verify_webhook(notification(name="中文 A&B=1"), {}).state
        == "paid"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"pid": "1002"},
        {"type": "wxpay"},
        {"notify_url": "https://attacker.example.com"},
        {"return_url": "https://gateway.example.com?trade_status=TRADE_SUCCESS"},
        {"extra": "field"},
        {"param": "x&money=0.01"},
        {"trade_no": "x=1"},
        {"out_trade_no": "not-uuid"},
    ],
)
def test_callback_rejects_unknown_or_smuggled_form_even_with_matching_digest(
    monkeypatch, config, change
):
    mock_gateway(monkeypatch, lambda *args: pytest.fail("invalid callback must not query"))
    with pytest.raises(ProviderError):
        ep.EasyPayProvider(config).verify_webhook(notification(**change), HEADERS)


@pytest.mark.parametrize(
    "fault", ["duplicate", "signature", "algorithm", "malformed", "content-type"]
)
def test_callback_wire_rejections(monkeypatch, config, fault):
    mock_gateway(monkeypatch, lambda *args: pytest.fail("invalid callback must not query"))
    raw, headers = notification(), HEADERS
    if fault == "duplicate":
        raw += b"&pid=1001"
    elif fault == "signature":
        raw = raw.replace(b"money=12.99", b"money=99.99")
    elif fault == "algorithm":
        raw = raw.replace(b"sign_type=MD5", b"sign_type=RSA2")
    elif fault == "malformed":
        raw += b"&param=%ZZ"
    else:
        headers = {"Content-Type": "application/json"}
    with pytest.raises(ProviderError):
        ep.EasyPayProvider(config).verify_webhook(raw, headers)


@pytest.mark.parametrize(
    "result",
    [
        paid(status=0),
        paid(money="12.98"),
        paid(trade_no="other"),
        paid(code=0),
        paid(data={"trade_no": "other"}),
    ],
)
def test_signed_success_callback_still_rejected_when_query_disagrees(monkeypatch, config, result):
    mock_gateway(monkeypatch, lambda *args: result)
    with pytest.raises(ProviderError):
        ep.EasyPayProvider(config).verify_webhook(notification(), HEADERS)


@pytest.mark.parametrize("success", ["0", "1"])
def test_refund_exact_configured_success_code_and_amount(monkeypatch, config, order, success):
    def gateway(endpoint, query, form):
        if query.get("act") == "order":
            return paid()
        assert query == {"act": "refund"}
        assert form == {
            "pid": "1001",
            "key": "test_secret_key",
            "money": "5.00",
            "out_trade_no": UUID(ORDER_ID).hex,
        }
        return {"code": success, "msg": "success"}

    mock_gateway(monkeypatch, gateway)
    evidence = ep.EasyPayProvider({**config, "refund_success_code": success}).create_refund(
        order, PAYMENT_ID, REFUND_ID, 500
    )
    assert evidence.state == "succeeded"
    assert evidence.amount_minor == 500 and evidence.currency == "cny"
    assert evidence.provider_refund_id is None


def test_refund_uses_trade_number_only_after_definite_order_not_found(monkeypatch, config, order):
    attempts = []

    def gateway(endpoint, query, form):
        if query.get("act") == "order":
            return paid()
        attempts.append(form)
        if "out_trade_no" in form:
            return {"code": -1, "msg": "订单编号不存在"}
        assert form["trade_no"] == PAYMENT_ID
        return {"code": 1}

    mock_gateway(monkeypatch, gateway)
    assert (
        ep.EasyPayProvider(config).create_refund(order, PAYMENT_ID, REFUND_ID, 500).state
        == "succeeded"
    )
    assert len(attempts) == 2


def test_refund_accepts_unambiguous_nested_final_result(monkeypatch, config, order):
    def gateway(endpoint, query, form):
        if query.get("act") == "order":
            return paid()
        return {
            "code": "1",
            "data": {
                "code": 1,
                "refund_status": "SUCCESS",
                "money": "5.00",
                "trade_no": PAYMENT_ID,
            },
        }

    mock_gateway(monkeypatch, gateway)
    assert (
        ep.EasyPayProvider(config).create_refund(order, PAYMENT_ID, REFUND_ID, 500).state
        == "succeeded"
    )


@pytest.mark.parametrize(
    "failure", ["network", "format", "unknown", "not-found-in-description", "bool"]
)
def test_refund_never_falls_back_or_reissues_after_ambiguous_dispatch(
    monkeypatch, config, order, failure
):
    attempted = []

    def gateway(endpoint, query, form):
        if query.get("act") == "order":
            return paid()
        attempted.append(form)
        if failure == "network":
            raise ProviderError("https://gateway.example.com/api.php?key=test_secret_key")
        if failure == "format":
            return None
        if failure == "unknown":
            return {"code": 0, "msg": "system error; may already be processed"}
        if failure == "bool":
            return {"code": True}
        return {"code": 0, "msg": "order not found in reconciliation, processing unknown"}

    mock_gateway(monkeypatch, gateway)
    with pytest.raises(ProviderError) as caught:
        ep.EasyPayProvider(config).create_refund(order, PAYMENT_ID, REFUND_ID, 500)
    assert len(attempted) == 1
    assert config["pkey"] not in str(caught.value)
    assert "https://" not in str(caught.value)


def test_refund_preflight_prevents_refunding_another_payment(monkeypatch, config, order):
    def gateway(endpoint, query, form):
        assert query["act"] == "order"
        return paid(trade_no="different-payment")

    mock_gateway(monkeypatch, gateway)
    with pytest.raises(ProviderError):
        ep.EasyPayProvider(config).create_refund(order, PAYMENT_ID, REFUND_ID, 500)


def test_refund_query_does_not_guess_from_order_status(monkeypatch, config, order):
    mock_gateway(monkeypatch, lambda *args: pytest.fail("classic V1 has no single-refund query"))
    with pytest.raises(ProviderError, match="核对"):
        ep.EasyPayProvider(config).query_refund(order, PAYMENT_ID, REFUND_ID, None)


@pytest.mark.parametrize(
    "result",
    [
        {"code": 1, "status": "pending"},
        {"code": 1, "refund_status": "PROCESSING"},
        {"code": 1, "msg": "订单不存在"},
        {"code": 0, "msg": "not exist"},
        {"code": 0, "msg": "订单不存在", "pid": "other"},
        {"code": 1, "data": {"code": 0, "refund_status": "PROCESSING", "money": "9.99"}},
        {"code": 1, "data": {"refund_status": "PROCESSING"}},
        {"code": 1, "data": {"money": "9.99"}},
        {"code": 1, "data": {"trade_no": "other-payment"}},
        {"code": 1, "data": {"msg": "订单不存在"}},
        {"code": 0, "msg": "订单不存在", "data": {"msg": "processing"}},
        {"code": 0, "msg": "订单不存在", "data": {"refund_status": "PROCESSING"}},
        {"code": 0, "msg": "订单不存在", "data": {"code": 1}},
        {"code": 0, "msg": "订单不存在", "status": "SUCCESS"},
    ],
)
def test_refund_contradictions_neither_complete_nor_fallback(monkeypatch, config, order, result):
    attempts = []

    def gateway(endpoint, query, form):
        if query.get("act") == "order":
            return paid()
        attempts.append(form)
        return result

    mock_gateway(monkeypatch, gateway)
    with pytest.raises(ProviderError):
        ep.EasyPayProvider(config).create_refund(order, PAYMENT_ID, REFUND_ID, 500)
    assert len(attempts) == 1


def test_close_only_queries_and_never_infers_cancellation(monkeypatch, config, order):
    mock_gateway(monkeypatch, lambda *args: paid(status=0))
    assert ep.EasyPayProvider(config).close_order(order, UUID(ORDER_ID).hex).state == "pending"


def test_wrong_provider_reference_does_not_query(monkeypatch, config, order):
    mock_gateway(monkeypatch, lambda *args: pytest.fail("must not query unrelated order"))
    with pytest.raises(ProviderError):
        ep.EasyPayProvider(config).query_order(order, UUID(REFUND_ID).hex)
