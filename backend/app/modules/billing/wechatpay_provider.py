"""WeChat Pay API v3 direct-merchant Native payments, public-key mode only.

Protocol references (reviewed 2026-11-22):
https://pay.wechatpay.cn/doc/v3/merchant/4012791877 (Native checkout)
https://pay.wechatpay.cn/doc/v3/merchant/4012791880 (query by merchant order)
https://pay.wechatpay.cn/doc/v3/merchant/4012791881 (close order)
https://pay.wechatpay.cn/doc/v3/merchant/4012791882 (payment notification)
https://pay.wechatpay.cn/doc/v3/merchant/4012791883 (refund)
https://pay.wechatpay.cn/doc/v3/merchant/4012791884 (refund query)
https://pay.wechatpay.cn/doc/v3/merchant/4013053249 (signature verification)
https://pay.wechatpay.cn/doc/v3/merchant/4012071382 (AES-GCM notification)

The service must retain the order's config snapshot, including its verification
key. No untrusted key/certificate downloads or alternative gateway URLs exist.
"""

import base64
import json
import re
import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidSignature, InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .providers import Checkout, CheckoutRequest, PaymentEvidence, ProviderError, RefundEvidence

_ORIGIN = "https://api.mch.weixin.qq.com"
_SIGNATURE_TYPE = "WECHATPAY2-SHA256-RSA2048"
_MAX_BODY = 1024 * 1024
_CLOCK_TOLERANCE = 300
_MAX_MINOR = 2_147_483_647


def _error() -> ProviderError:
    return ProviderError("微信支付返回结果无法确认，请保留原订单并稍后查询")


def _keys(config: dict[str, str]) -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    try:
        private = serialization.load_pem_private_key(
            config["merchant_private_key"].encode(), password=None
        )
        public = serialization.load_pem_public_key(config["platform_public_key"].encode())
        if not isinstance(private, rsa.RSAPrivateKey) or private.key_size != 2048:
            raise ValueError
        if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 2048:
            raise ValueError
        return private, public
    except (ValueError, TypeError, KeyError, UnsupportedAlgorithm):
        raise ProviderError("微信支付需要有效的 RSA 2048 商户私钥和微信支付公钥") from None


def validate_wechatpay_config(config: dict[str, str]) -> None:
    patterns = {
        "app_id": r"wx[A-Za-z0-9]{16}",
        "mch_id": r"[0-9]{6,32}",
        "merchant_serial": r"[0-9A-Fa-f]{1,64}",
        "api_v3_key": r"[A-Za-z0-9]{32}",
        "platform_public_key_id": r"PUB_KEY_ID_[0-9]{10,64}",
    }
    if any(
        not isinstance(config.get(key), str) or not re.fullmatch(pattern, config[key])
        for key, pattern in patterns.items()
    ):
        raise ProviderError("微信支付商户、公钥 ID 或 API v3 配置格式无效")
    _keys(config)


def _uuid_hex(value: str) -> str:
    try:
        return UUID(value).hex
    except (ValueError, TypeError, AttributeError):
        raise ProviderError("支付订单编号格式无效") from None


def _minor(value: object) -> int:
    if type(value) is not int or not 0 < value <= _MAX_MINOR:
        raise _error()
    return value


def _object(raw: bytes) -> dict:
    try:
        if len(raw) > _MAX_BODY:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise _error() from None


def _transaction_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise _error()
    return value


class WeChatPayProvider:
    def __init__(self, config: dict[str, str]):
        validate_wechatpay_config(config)
        self._config = dict(config)
        self._private, self._public = _keys(config)

    def _verify_signature(self, raw: bytes, headers: Mapping[str, str]) -> None:
        try:
            normalized: dict[str, str] = {}
            for key, value in headers.items():
                key = key.lower()
                if key.startswith("wechatpay-") and key in normalized:
                    raise ValueError
                normalized[key] = value
            timestamp = normalized["wechatpay-timestamp"]
            nonce = normalized["wechatpay-nonce"]
            if (
                len(raw) > _MAX_BODY
                or not re.fullmatch(r"[0-9]{10,12}", timestamp)
                or abs(time.time() - int(timestamp)) > _CLOCK_TOLERANCE
                or not re.fullmatch(r"[\x21-\x7e]{1,128}", nonce)
                or normalized["wechatpay-serial"] != self._config["platform_public_key_id"]
                or normalized.get("wechatpay-signature-type", _SIGNATURE_TYPE) != _SIGNATURE_TYPE
            ):
                raise ValueError
            signature = base64.b64decode(normalized["wechatpay-signature"], validate=True)
            message = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw + b"\n"
            self._public.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
        except (KeyError, ValueError, TypeError, UnicodeError, InvalidSignature):
            raise ProviderError("微信支付签名验证失败") from None

    def _request(
        self, method: str, path: str, body: dict | None = None, *, allow_empty: bool = False
    ) -> dict:
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body else b""
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode() + raw + b"\n"
        signature = base64.b64encode(
            self._private.sign(message, padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        authorization = (
            f'{_SIGNATURE_TYPE} mchid="{self._config["mch_id"]}",'
            f'nonce_str="{nonce}",timestamp="{timestamp}",'
            f'serial_no="{self._config["merchant_serial"]}",signature="{signature}"'
        )
        try:
            with httpx.Client(
                timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False, trust_env=False
            ) as client:
                with client.stream(
                    method,
                    _ORIGIN + path,
                    content=raw,
                    headers={
                        "Authorization": authorization,
                        "Wechatpay-Serial": self._config["platform_public_key_id"],
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Content-Type": "application/json",
                        "User-Agent": "Inspiration-Billing/1.0",
                    },
                ) as response:
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise _error()
                    chunks: list[bytes] = []
                    length = 0
                    for chunk in response.iter_bytes():
                        length += len(chunk)
                        if length > _MAX_BODY:
                            raise _error()
                        chunks.append(chunk)
                    response_raw = b"".join(chunks)
                    # Never trust even a status/error result without authenticating it.
                    self._verify_signature(response_raw, response.headers)
                    if not 200 <= response.status_code < 300:
                        raise _error()
                    if allow_empty and response.status_code == 204 and not response_raw:
                        return {}
                    return _object(response_raw)
        except httpx.HTTPError:
            raise _error() from None

    @staticmethod
    def _validate_order(order: CheckoutRequest) -> str:
        out_trade_no = _uuid_hex(order.order_id)
        _minor(order.amount_minor)
        if order.currency != "cny":
            raise ProviderError("微信支付 Native 仅支持人民币 CNY")
        return out_trade_no

    def create_checkout(self, order: CheckoutRequest) -> Checkout:
        out_trade_no = self._validate_order(order)
        try:
            callback = urlsplit(order.webhook_url)
            valid_callback = (
                callback.scheme == "https"
                and callback.hostname
                and not callback.username
                and not callback.password
                and not callback.fragment
                and callback.port in (None, 443)
                and len(order.webhook_url) <= 255
                and all(ord(char) >= 33 for char in order.webhook_url)
            )
        except (ValueError, TypeError):
            valid_callback = False
        if not valid_callback:
            raise ProviderError("微信支付回调必须使用有效的 HTTPS 地址")
        if (
            order.expires_at.tzinfo is None
            or order.expires_at <= datetime.now(UTC)
            or not order.title.strip()
        ):
            raise ProviderError("微信支付订单有效期或商品描述无效")
        data = self._request(
            "POST",
            "/v3/pay/transactions/native",
            {
                "appid": self._config["app_id"],
                "mchid": self._config["mch_id"],
                "description": order.title[:127],
                "out_trade_no": out_trade_no,
                "time_expire": order.expires_at.astimezone(UTC).isoformat(timespec="seconds"),
                "notify_url": order.webhook_url,
                "amount": {"total": order.amount_minor, "currency": "CNY"},
            },
        )
        code_url = data.get("code_url")
        if (
            not isinstance(code_url, str)
            or len(code_url) > 2048
            or not code_url.startswith("weixin://wxpay/bizpayurl?")
            or any(ord(char) < 33 or ord(char) > 126 for char in code_url)
        ):
            raise _error()
        return Checkout(provider_order_id=out_trade_no, qr_payload=code_url)

    def _payment(self, data: dict) -> PaymentEvidence:
        if (
            data.get("mchid") != self._config["mch_id"]
            or data.get("appid") != self._config["app_id"]
            or data.get("trade_type", "NATIVE") != "NATIVE"
        ):
            raise _error()
        out_trade_no = data.get("out_trade_no")
        if not isinstance(out_trade_no, str) or not re.fullmatch(r"[0-9a-f]{32}", out_trade_no):
            raise _error()
        trade_state = data.get("trade_state")
        if not isinstance(trade_state, str):
            raise _error()
        state = {
            "SUCCESS": "paid",
            "NOTPAY": "pending",
            "USERPAYING": "pending",
            "CLOSED": "expired",
        }.get(trade_state)
        if state is None:
            # REFUND is not evidence permitting fresh credit, nor a failed payment.
            raise _error()
        amount = data.get("amount")
        amount_minor, currency = None, None
        if amount is not None:
            if not isinstance(amount, dict) or amount.get("currency") != "CNY":
                raise _error()
            amount_minor, currency = _minor(amount.get("total")), "cny"
        payment_id = data.get("transaction_id")
        if state == "paid":
            if amount_minor is None or data.get("trade_type") != "NATIVE":
                raise _error()
            payment_id = _transaction_id(payment_id)
        elif payment_id is not None:
            payment_id = _transaction_id(payment_id)
        return PaymentEvidence(
            order_id=str(UUID(hex=out_trade_no)),
            state=state,
            provider_order_id=out_trade_no,
            payment_id=payment_id,
            amount_minor=amount_minor,
            currency=currency,
        )

    def query_order(self, order: CheckoutRequest, provider_order_id: str | None) -> PaymentEvidence:
        out_trade_no = self._validate_order(order)
        if provider_order_id is not None and provider_order_id != out_trade_no:
            raise _error()
        query = urlencode({"mchid": self._config["mch_id"]})
        data = self._request("GET", f"/v3/pay/transactions/out-trade-no/{out_trade_no}?{query}")
        evidence = self._payment(data)
        if (
            evidence.provider_order_id != out_trade_no
            or (evidence.amount_minor is not None and evidence.amount_minor != order.amount_minor)
            or (evidence.currency is not None and evidence.currency != order.currency)
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
            self._request(
                "POST",
                f"/v3/pay/transactions/out-trade-no/{reference}/close",
                {"mchid": self._config["mch_id"]},
                allow_empty=True,
            )
        except ProviderError:
            # Some empty 204 responses lack a signature, or a concurrent payment
            # may reject close. Neither is evidence of cancellation on its own.
            pass
        # Even successful close is confirmed by a signed, identity-checked query.
        return self.query_order(order, provider_order_id)

    def verify_webhook(self, raw: bytes, headers: Mapping[str, str]) -> PaymentEvidence | None:
        self._verify_signature(raw, headers)
        envelope = _object(raw)
        if envelope.get("event_type") != "TRANSACTION.SUCCESS":
            # Refund outcomes are reconciled using the authenticated refund query.
            return None
        resource = envelope.get("resource")
        if (
            envelope.get("resource_type") != "encrypt-resource"
            or not isinstance(resource, dict)
            or resource.get("algorithm") != "AEAD_AES_256_GCM"
            or resource.get("original_type") != "transaction"
        ):
            raise _error()
        try:
            ciphertext = base64.b64decode(resource["ciphertext"], validate=True)
            plaintext = AESGCM(self._config["api_v3_key"].encode()).decrypt(
                resource["nonce"].encode(),
                ciphertext,
                resource.get("associated_data", "").encode(),
            )
        except (KeyError, ValueError, TypeError, AttributeError, InvalidTag):
            raise ProviderError("微信支付通知解密失败") from None
        evidence = self._payment(_object(plaintext))
        if evidence.state != "paid":
            raise _error()
        return evidence

    def _refund(
        self,
        data: dict,
        order: CheckoutRequest,
        payment_id: str,
        refund_id: str,
        provider_refund_id: str | None = None,
        expected_minor: int | None = None,
    ) -> RefundEvidence:
        if (
            data.get("out_trade_no") != _uuid_hex(order.order_id)
            or data.get("out_refund_no") != _uuid_hex(refund_id)
            or data.get("transaction_id") != payment_id
            or ("mchid" in data and data["mchid"] != self._config["mch_id"])
        ):
            raise _error()
        actual_id = _transaction_id(data.get("refund_id"))
        if provider_refund_id is not None and actual_id != provider_refund_id:
            raise _error()
        amount = data.get("amount")
        if (
            not isinstance(amount, dict)
            or amount.get("currency") != "CNY"
            or type(amount.get("total")) is not int
            or amount["total"] != order.amount_minor
        ):
            raise _error()
        refund_minor = _minor(amount.get("refund"))
        if refund_minor > order.amount_minor or (
            expected_minor is not None and refund_minor != expected_minor
        ):
            raise _error()
        # ABNORMAL still holds funds at WeChat. Never restore local refundable
        # credit or initiate another refund: an operator must resolve it there.
        status = data.get("status")
        if not isinstance(status, str):
            raise _error()
        state = {
            "SUCCESS": "succeeded",
            "CLOSED": "failed",
            "PROCESSING": "pending",
            "ABNORMAL": "pending",
        }.get(status)
        if state is None:
            raise _error()
        return RefundEvidence(
            state=state, provider_refund_id=actual_id, amount_minor=refund_minor, currency="cny"
        )

    def create_refund(
        self, order: CheckoutRequest, payment_id: str, refund_id: str, amount_minor: int
    ) -> RefundEvidence:
        self._validate_order(order)
        _transaction_id(payment_id)
        if _minor(amount_minor) > order.amount_minor:
            raise _error()
        # Validate the original payment before moving money. REFUND means the
        # original paid transaction already has a partial/full refund; it may
        # be queried here but must never be interpreted as new payment credit.
        query = urlencode({"mchid": self._config["mch_id"]})
        original = self._request(
            "GET", f"/v3/pay/transactions/out-trade-no/{_uuid_hex(order.order_id)}?{query}"
        )
        if original.get("trade_state") not in ("SUCCESS", "REFUND"):
            raise _error()
        original_payment = self._payment({**original, "trade_state": "SUCCESS"})
        if (
            original_payment.order_id != str(UUID(order.order_id))
            or original_payment.payment_id != payment_id
            or original_payment.amount_minor != order.amount_minor
            or original_payment.currency != order.currency
        ):
            raise _error()
        data = self._request(
            "POST",
            "/v3/refund/domestic/refunds",
            {
                "transaction_id": payment_id,
                "out_refund_no": _uuid_hex(refund_id),
                "notify_url": order.webhook_url,
                "amount": {"refund": amount_minor, "total": order.amount_minor, "currency": "CNY"},
            },
        )
        evidence = self._refund(data, order, payment_id, refund_id, expected_minor=amount_minor)
        # Acknowledgement of the application is not settlement. Even an apparent
        # immediate SUCCESS is confirmed by a separate query before completion.
        if evidence.state == "succeeded":
            return RefundEvidence(
                state="pending",
                provider_refund_id=evidence.provider_refund_id,
                amount_minor=evidence.amount_minor,
                currency=evidence.currency,
            )
        return evidence

    def query_refund(
        self,
        order: CheckoutRequest,
        payment_id: str,
        refund_id: str,
        provider_refund_id: str | None,
    ) -> RefundEvidence:
        self._validate_order(order)
        _transaction_id(payment_id)
        data = self._request("GET", f"/v3/refund/domestic/refunds/{_uuid_hex(refund_id)}")
        return self._refund(data, order, payment_id, refund_id, provider_refund_id)
