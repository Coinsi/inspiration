"""Alipay direct-merchant PC hosted payments, RSA2 public-key mode.

Official references reviewed 2026-11-22:
https://aipay.alipay.com/docs/vibe-pay/ai-web-app-payment-qianyi/api-list/alipay-trade-page-pay.html
https://aipay.alipay.com/docs/vibe-pay/ai-web-app-payment-qianyi/api-list/alipay-trade-query.html
https://aipay.alipay.com/docs/vibe-pay/ai-web-app-payment-qianyi/api-list/alipay-trade-close.html
https://aipay.alipay.com/docs/vibe-pay/ai-web-app-payment-qianyi/api-list/alipay-trade-refund.html
https://aipay.alipay.com/docs/vibe-pay/ai-web-app-payment-qianyi/api-list/alipay-trade-fastpay-refund-query.html
https://github.com/alipay/alipay-sdk-python-all/tree/40ebb5e37f6f167635ee814b79772f6b46af990b

Gateway response signatures cover the original response JSON object bytes, not
reserialized JSON. Form notifications use decoded, sorted parameters, excluding
sign/sign_type. The caller must acknowledge accepted notifications with the
literal text 'success', and persist/compare the order before granting credit.
"""

import base64
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .providers import Checkout, CheckoutRequest, PaymentEvidence, ProviderError, RefundEvidence

_GATEWAY = "https://openapi.alipay.com/gateway.do"
_CHINA = timezone(timedelta(hours=8))
_MAX_BODY = 1024 * 1024
_MAX_MINOR = 10_000_000_000


def _error() -> ProviderError:
    return ProviderError("支付宝返回结果无法确认，请保留原订单并稍后查询")


def _keys(config: dict[str, str]) -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    try:
        private = serialization.load_pem_private_key(config["app_private_key"].encode(), None)
        public = serialization.load_pem_public_key(config["alipay_public_key"].encode())
        if not isinstance(private, rsa.RSAPrivateKey) or private.key_size != 2048:
            raise ValueError
        if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 2048:
            raise ValueError
        return private, public
    except (KeyError, ValueError, TypeError, UnsupportedAlgorithm):
        raise ProviderError("支付宝需要有效的 RSA 2048 应用私钥和支付宝公钥 PEM") from None


def validate_alipay_config(config: dict[str, str]) -> None:
    if (
        not isinstance(config, dict)
        or set(config) != {"app_id", "seller_id", "app_private_key", "alipay_public_key"}
        or any(not isinstance(value, str) or len(value) > 16384 for value in config.values())
        or not re.fullmatch(r"[0-9]{16}", config.get("app_id", ""))
        or not re.fullmatch(r"2088[0-9]{12}", config.get("seller_id", ""))
    ):
        raise ProviderError("支付宝应用 ID、商户 UID 或密钥配置格式无效")
    _keys(config)


def _uuid_hex(value: object) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return UUID(value).hex
    except (ValueError, TypeError, AttributeError):
        raise _error() from None


def _amount(value: object) -> int:
    if type(value) is not int or not 0 < value <= _MAX_MINOR:
        raise _error()
    return value


def _yuan(value: int) -> str:
    _amount(value)
    return f"{value // 100}.{value % 100:02d}"


def _minor(value: object) -> int:
    # JSON decimal numbers are parsed as Decimal; binary float is never accepted.
    if not isinstance(value, (str, Decimal)):
        raise _error()
    text = str(value)
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?", text):
        raise _error()
    whole, _, fraction = text.partition(".")
    return _amount(int(whole) * 100 + int(fraction.ljust(2, "0") or "0"))


def _trade_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{16,64}", value):
        raise _error()
    return value


def _secure_url(value: str) -> None:
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or parts.fragment
            or len(value) > 256
            or any(ord(char) <= 32 for char in value)
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ProviderError("支付宝返回及回调地址必须是有效的 HTTPS 地址") from None


def _canonical(params: Mapping[str, str]) -> bytes:
    return "&".join(
        f"{key}={value}" for key, value in sorted(params.items()) if value != ""
    ).encode()


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _response_parts(raw: bytes, key: str) -> tuple[dict, bytes, str]:
    """Extract exactly one top-level response node and its original signed bytes."""
    try:
        if len(raw) > _MAX_BODY:
            raise ValueError
        text = raw.decode("utf-8")
        decoder = json.JSONDecoder(object_pairs_hook=_unique_object, parse_float=Decimal)
        root = decoder.decode(text)
        if (
            not isinstance(root, dict)
            or key not in root
            or not isinstance(root[key], dict)
            or not isinstance(root.get("sign"), str)
            or any(name.endswith("_response") and name != key for name in root)
        ):
            raise ValueError
        position = len(text) - len(text.lstrip()) + 1  # opening brace was checked by decoder
        while position < len(text):
            while text[position].isspace():
                position += 1
            name, position = decoder.raw_decode(text, position)
            while text[position].isspace():
                position += 1
            if text[position] != ":":
                raise ValueError
            position += 1
            while text[position].isspace():
                position += 1
            start = position
            _, position = decoder.raw_decode(text, position)
            if name == key:
                return root[key], text[start:position].encode(), root["sign"]
            while text[position].isspace():
                position += 1
            if text[position] != ",":
                raise ValueError
            position += 1
        raise ValueError
    except (ValueError, TypeError, UnicodeError, IndexError, RecursionError):
        raise _error() from None


class AlipayProvider:
    def __init__(self, config: dict[str, str]):
        validate_alipay_config(config)
        self._config = dict(config)
        self._private, self._public = _keys(config)

    def _verify(self, message: bytes, signature: str) -> None:
        try:
            decoded = base64.b64decode(signature, validate=True)
            self._public.verify(decoded, message, padding.PKCS1v15(), hashes.SHA256())
        except (ValueError, TypeError, InvalidSignature):
            raise ProviderError("支付宝签名验证失败") from None

    def _params(self, method: str, biz: dict, **extra: str) -> dict[str, str]:
        params = {
            "app_id": self._config["app_id"],
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "version": "1.0",
            "timestamp": datetime.now(_CHINA).strftime("%Y-%m-%d %H:%M:%S"),
            "biz_content": json.dumps(biz, ensure_ascii=False, separators=(",", ":")),
            **extra,
        }
        params["sign"] = base64.b64encode(
            self._private.sign(_canonical(params), padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        return params

    def _request(self, method: str, biz: dict) -> dict:
        params = self._params(method, biz)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False
            ) as client:
                with client.stream(
                    "POST",
                    _GATEWAY,
                    data=params,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "User-Agent": "Inspiration-Billing/1.0",
                    },
                ) as response:
                    if (
                        not 200 <= response.status_code < 300
                        or response.headers.get("Content-Encoding", "identity").lower()
                        != "identity"
                    ):
                        raise _error()
                    chunks, length = [], 0
                    for chunk in response.iter_bytes():
                        length += len(chunk)
                        if length > _MAX_BODY:
                            raise _error()
                        chunks.append(chunk)
            data, signed, signature = _response_parts(
                b"".join(chunks), method.replace(".", "_") + "_response"
            )
            self._verify(signed, signature)
            # Business errors, including NOT_EXIST and SYSTEM_ERROR, do not
            # establish that a previous payment/refund could not have succeeded.
            if data.get("code") != "10000":
                raise _error()
            return data
        except httpx.HTTPError:
            raise _error() from None

    @staticmethod
    def _validate_order(order: CheckoutRequest) -> str:
        reference = _uuid_hex(order.order_id)
        _amount(order.amount_minor)
        if order.currency != "cny":
            raise ProviderError("支付宝电脑网站支付仅支持人民币 CNY")
        return reference

    def create_checkout(self, order: CheckoutRequest) -> Checkout:
        reference = self._validate_order(order)
        _secure_url(order.success_url)
        _secure_url(order.webhook_url)
        if order.expires_at.tzinfo is None:
            raise _error()
        lifetime = (order.expires_at - datetime.now(UTC)).total_seconds()
        if not 60 <= lifetime <= 15 * 86400:
            raise ProviderError("支付宝订单有效期必须在 1 分钟至 15 天之间")
        title = re.sub(r"[/=&\x00-\x1f]", " ", order.title).strip()[:256]
        if not title:
            raise ProviderError("支付宝订单标题无效")
        params = self._params(
            "alipay.trade.page.pay",
            {
                "out_trade_no": reference,
                "total_amount": _yuan(order.amount_minor),
                "subject": title,
                "product_code": "FAST_INSTANT_TRADE_PAY",
                "time_expire": order.expires_at.astimezone(_CHINA).strftime("%Y-%m-%d %H:%M:%S"),
            },
            return_url=order.success_url,
            notify_url=order.webhook_url,
        )
        # Official SDK page_execute(GET) is a locally generated signed URL.
        # Only app ID and signature are public; the private key is never sent.
        return Checkout(
            provider_order_id=reference, checkout_url=_GATEWAY + "?" + urlencode(params)
        )

    def _payment(self, data: dict, *, callback: bool = False) -> PaymentEvidence:
        if callback and (
            data.get("app_id") != self._config["app_id"]
            or data.get("seller_id") != self._config["seller_id"]
        ):
            raise _error()
        for key, expected in (
            ("app_id", self._config["app_id"]),
            ("auth_app_id", self._config["app_id"]),
            ("seller_id", self._config["seller_id"]),
        ):
            if key in data and data[key] != expected:
                raise _error()
        for key in ("currency", "trans_currency"):
            if key in data and data[key] != "CNY":
                raise _error()
        reference = data.get("out_trade_no")
        if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{32}", reference):
            raise _error()
        status = data.get("trade_status")
        if not isinstance(status, str):
            raise _error()
        state = {
            "TRADE_SUCCESS": "paid",
            "TRADE_FINISHED": "paid",
            "WAIT_BUYER_PAY": "pending",
            "TRADE_CLOSED": "expired",
        }.get(status)
        if state is None:
            raise _error()
        payment_id = data.get("trade_no")
        if state == "paid" or payment_id is not None:
            payment_id = _trade_id(payment_id)
        return PaymentEvidence(
            order_id=str(UUID(hex=reference)),
            state=state,
            provider_order_id=reference,
            payment_id=payment_id,
            amount_minor=_minor(data.get("total_amount")),
            currency="cny",
        )

    def query_order(self, order: CheckoutRequest, provider_order_id: str | None) -> PaymentEvidence:
        reference = self._validate_order(order)
        if provider_order_id is not None and provider_order_id != reference:
            raise _error()
        # Signed, authenticated direct-app requests scope this query to its
        # merchant. Alipay's query response does not promise app_id/seller_id.
        data = self._request("alipay.trade.query", {"out_trade_no": reference})
        evidence = self._payment(data)
        if (
            evidence.provider_order_id != reference
            or evidence.amount_minor != order.amount_minor
            or evidence.currency != order.currency
        ):
            raise _error()
        return evidence

    def close_order(self, order: CheckoutRequest, provider_order_id: str) -> PaymentEvidence:
        reference = self._validate_order(order)
        if provider_order_id != reference:
            raise _error()
        original = self.query_order(order, provider_order_id)
        if original.state in ("paid", "expired"):
            return original
        try:
            result = self._request("alipay.trade.close", {"out_trade_no": reference})
            if result.get("out_trade_no") != reference or (
                original.payment_id is not None and result.get("trade_no") != original.payment_id
            ):
                raise _error()
        except ProviderError:
            # A racing payment or unknown close result must be resolved from the
            # original signed query; never infer canceled from a gateway error.
            pass
        return self.query_order(order, provider_order_id)

    def verify_webhook(self, raw: bytes, headers: Mapping[str, str]) -> PaymentEvidence | None:
        content_type = {key.lower(): value for key, value in headers.items()}.get(
            "content-type", ""
        )
        if content_type.split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            raise ProviderError("支付宝通知必须使用表单格式")
        try:
            if len(raw) > _MAX_BODY:
                raise ValueError
            text = raw.decode("utf-8")
            if re.search(r"%(?![0-9a-fA-F]{2})", text):
                raise ValueError
            pairs = parse_qsl(
                text,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=100,
            )
            params = _unique_object(pairs)
            if any(
                not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                or any(char in value for char in "\r\n\x00")
                for key, value in params.items()
            ):
                raise ValueError
            if (
                params.get("sign_type") != "RSA2"
                or not params.get("sign")
                or params.get("charset", "utf-8").lower() != "utf-8"
                or params.get("notify_type") != "trade_status_sync"
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise ProviderError("支付宝通知格式无效") from None
        signature = params.pop("sign")
        params.pop("sign_type")
        self._verify(_canonical(params), signature)
        # Alipay may retry an older signed notification; unlike a fresh API
        # request it has no short replay window. Order idempotency handles replay.
        return self._payment(params, callback=True)

    def create_refund(
        self, order: CheckoutRequest, payment_id: str, refund_id: str, amount_minor: int
    ) -> RefundEvidence:
        reference = self._validate_order(order)
        request_id = _uuid_hex(refund_id)
        _trade_id(payment_id)
        if _amount(amount_minor) > order.amount_minor:
            raise _error()
        original = self.query_order(order, reference)
        if original.state != "paid" or original.payment_id != payment_id:
            raise _error()
        data = self._request(
            "alipay.trade.refund",
            {
                "out_trade_no": reference,
                "out_request_no": request_id,
                "refund_amount": _yuan(amount_minor),
            },
        )
        if data.get("out_trade_no") != reference or data.get("trade_no") != payment_id:
            raise _error()
        if "out_request_no" in data and data["out_request_no"] != request_id:
            raise _error()
        # refund_fee can represent a cumulative amount. Do not compare it with
        # this partial refund or infer settlement from code/fund_change alone.
        return RefundEvidence(
            state="pending",
            provider_refund_id=request_id,
            amount_minor=amount_minor,
            currency="cny",
        )

    def query_refund(
        self,
        order: CheckoutRequest,
        payment_id: str,
        refund_id: str,
        provider_refund_id: str | None,
    ) -> RefundEvidence:
        reference = self._validate_order(order)
        request_id = _uuid_hex(refund_id)
        _trade_id(payment_id)
        if provider_refund_id is not None and provider_refund_id != request_id:
            raise _error()
        data = self._request(
            "alipay.trade.fastpay.refund.query",
            {
                "out_trade_no": reference,
                "out_request_no": request_id,
            },
        )
        for key, expected in (
            ("out_trade_no", reference),
            ("trade_no", payment_id),
            ("out_request_no", request_id),
        ):
            if key in data and data[key] != expected:
                raise _error()
        status = data.get("refund_status")
        if status is None or status == "":
            # A successful query with no status is not proof of final failure.
            return RefundEvidence(state="pending", provider_refund_id=request_id)
        if status != "REFUND_SUCCESS":
            raise _error()
        if (
            data.get("out_trade_no") != reference
            or data.get("trade_no") != payment_id
            or data.get("out_request_no") != request_id
            or _minor(data.get("total_amount")) != order.amount_minor
        ):
            raise _error()
        refunded = _minor(data.get("refund_amount"))
        if refunded > order.amount_minor:
            raise _error()
        return RefundEvidence(
            state="succeeded", provider_refund_id=request_id, amount_minor=refunded, currency="cny"
        )
