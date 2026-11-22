"""EasyPay V1 compatibility, with authenticated query confirmation before credit.

Protocol reference: Wei-Shaw/sub2api v0.2.24, fixed source reviewed 2026-11-22:
https://github.com/Wei-Shaw/sub2api/blob/328db26f450edbdf9f8d4344a3ea903ac6396842/backend/internal/payment/provider/easypay.go

Classic V1 has no universally idempotent refund or single-refund query. Callers
must durably mark a refund dispatched before calling create_refund, never retry
an ambiguous dispatch, and route uncertain outcomes to manual reconciliation.
"""

import hashlib
import hmac
import ipaddress
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit
from uuid import UUID

from .easypay_http import normalize_api_base, request_json
from .providers import Checkout, CheckoutRequest, PaymentEvidence, ProviderError, RefundEvidence

_REQUIRED_CONFIG = {"api_base", "pid", "pkey", "payment_type", "payment_mode"}
_OPTIONAL_CONFIG = {"channel_id", "refund_success_code"}
_NOTIFY_KEYS = {
    "pid",
    "trade_no",
    "out_trade_no",
    "type",
    "name",
    "money",
    "trade_status",
    "param",
    "sign",
    "sign_type",
}
_MAX_BODY = 1024 * 1024
_MAX_MINOR = 10_000_000_000


def _error() -> ProviderError:
    return ProviderError("易支付返回结果无法确认，请保留原订单并稍后核对")


def validate_easypay_config(config: dict[str, str]) -> None:
    if (
        not isinstance(config, dict)
        or not _REQUIRED_CONFIG <= set(config)
        or set(config) - (_REQUIRED_CONFIG | _OPTIONAL_CONFIG)
        or any(not isinstance(value, str) or len(value) > 4096 for value in config.values())
        or any(not config[key].strip() for key in _REQUIRED_CONFIG)
    ):
        raise ProviderError("易支付配置字段不完整或格式无效")
    if (
        not re.fullmatch(r"[0-9]{1,32}", config["pid"])
        or not re.fullmatch(r"[\x21-\x7e]{1,256}", config["pkey"])
        or config["payment_type"] not in ("alipay", "wxpay")
        or config["payment_mode"] not in ("qrcode", "popup")
        or config.get("refund_success_code", "1") not in ("", "0", "1")
        or (config.get("channel_id") and not re.fullmatch(r"[0-9]{1,32}", config["channel_id"]))
    ):
        raise ProviderError("易支付商户、支付方式或退款成功码配置无效")
    normalize_api_base(config["api_base"])


def _uuid_hex(value: object) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return UUID(value).hex
    except (ValueError, TypeError, AttributeError):
        raise _error() from None


def _minor_units(value: object) -> int:
    if type(value) is not int or not 0 < value <= _MAX_MINOR:
        raise _error()
    return value


def _yuan(value: int) -> str:
    _minor_units(value)
    return f"{value // 100}.{value % 100:02d}"


def _scalar(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if type(value) is int or isinstance(value, Decimal):
        return str(value)
    raise _error()


def _money(value: object) -> int:
    text = _scalar(value)
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?", text):
        raise _error()
    whole, _, fraction = text.partition(".")
    return _minor_units(int(whole) * 100 + int(fraction.ljust(2, "0") or "0"))


def _code(value: object) -> int:
    text = _scalar(value)
    if not re.fullmatch(r"-?[0-9]{1,9}", text):
        raise _error()
    return int(text)


def _trade_id(value: object) -> str:
    text = _scalar(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", text):
        raise _error()
    return text


def _sign(params: Mapping[str, str], key: str) -> str:
    content = (
        "&".join(
            f"{name}={value}"
            for name, value in sorted(params.items())
            if name not in ("sign", "sign_type") and value != ""
        )
        + key
    )
    # Required by the legacy wire protocol. Active order confirmation is also
    # mandatory; the digest alone never authorizes credit.
    return hashlib.md5(content.encode("utf-8"), usedforsecurity=False).hexdigest()


def _https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or "\\" in value
            or len(value) > 4096
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or not re.fullmatch(r"[A-Za-z0-9.-]+", host)
            or "." not in host
            # WHATWG browsers normalize short, octal and hexadecimal IPv4
            # spellings. A DNS-only alphabetic TLD excludes all such forms.
            or not re.fullmatch(r"[A-Za-z]{2,63}", host.rsplit(".", 1)[-1])
            or host.endswith(
                (".localhost", ".local", ".internal", ".test", ".invalid", ".lan", ".home")
            )
            or any(
                not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                for label in host.split(".")
            )
        ):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ProviderError("易支付返回了不受支持的支付地址") from None


def _field(data: dict, key: str) -> object:
    nested = data.get("data")
    if nested is not None and not isinstance(nested, dict):
        raise _error()
    top, inner = data.get(key), (nested or {}).get(key)
    if top is not None and inner is not None and _scalar(top) != _scalar(inner):
        raise _error()
    return top if top is not None else inner


class EasyPayProvider:
    def __init__(self, config: dict[str, str]):
        validate_easypay_config(config)
        self._config = dict(config)
        self._base = normalize_api_base(config["api_base"])
        self._refund_success = int(config.get("refund_success_code") or "1")

    def _request(self, endpoint: str, *, query=None, form=None) -> dict:
        try:
            result = request_json(self._base, endpoint, query=query, form=form)
            if not isinstance(result, dict):
                raise _error()
            return result
        except (ProviderError, ValueError, TypeError):
            # api.php query strings carry the key. Do not retain an upstream
            # URL, response message or exception chain in the public error.
            raise _error() from None

    @staticmethod
    def _validate_order(order: CheckoutRequest) -> str:
        reference = _uuid_hex(order.order_id)
        _minor_units(order.amount_minor)
        if order.currency != "cny":
            raise ProviderError("易支付 V1 仅支持人民币 CNY")
        return reference

    def _public_ref(self, value: object, *, qr: bool) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise _error()
        value = value.strip()
        if (
            not value
            or len(value) > 4096
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or "\\" in value
            or value.startswith("//")
        ):
            raise _error()
        if value.startswith("/"):
            value = urljoin(self._base + "/", value)
        if self._config["pkey"] in unquote(unquote(value)):
            raise ProviderError("易支付返回地址包含敏感配置，已拒绝展示")
        try:
            parsed = urlsplit(value)
            scheme = parsed.scheme.lower()
        except ValueError:
            raise _error() from None
        if scheme == "https":
            return _https_url(value)
        if qr and (
            (
                scheme in ("weixin", "wxp", "alipays", "alipay")
                and value.startswith(scheme + "://")
                and parsed.netloc
                and parsed.username is None
            )
            or (not scheme and re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", value))
        ):
            return value
        raise ProviderError("易支付返回了不受支持的支付地址")

    def create_checkout(self, order: CheckoutRequest) -> Checkout:
        reference = self._validate_order(order)
        _https_url(order.webhook_url)
        _https_url(order.success_url)
        if order.expires_at.tzinfo is None or order.expires_at <= datetime.now(UTC):
            raise ProviderError("支付订单已过期")
        title = order.title.strip()[:128]
        if not title or any(ord(char) < 32 for char in title):
            raise ProviderError("易支付订单标题无效")
        params = {
            "pid": self._config["pid"],
            "type": self._config["payment_type"],
            "out_trade_no": reference,
            "notify_url": order.webhook_url,
            "return_url": order.success_url,
            "name": title,
            "money": _yuan(order.amount_minor),
        }
        if self._config.get("channel_id"):
            params["cid"] = self._config["channel_id"]
        if order.is_mobile:
            params["device"] = "mobile"
        if self._config["payment_mode"] == "qrcode":
            try:
                if not isinstance(order.client_ip, str) or "%" in order.client_ip:
                    raise ValueError
                params["clientip"] = str(ipaddress.ip_address(order.client_ip))
            except ValueError:
                raise ProviderError("易支付扫码下单需要有效的客户端 IP") from None
        params["sign"] = _sign(params, self._config["pkey"])
        params["sign_type"] = "MD5"
        if self._config["payment_mode"] == "popup":
            return Checkout(
                provider_order_id=reference,
                checkout_url=self._base + "/submit.php?" + urlencode(params),
            )
        result = self._request("mapi.php", form=params)
        if _code(result.get("code")) != 1:
            raise _error()
        self._echoes(result, reference)
        selected = (
            result.get("payurl2")
            if order.is_mobile and result.get("payurl2")
            else result.get("payurl")
        )
        qr_payload = self._public_ref(result.get("qrcode"), qr=True)
        checkout_url = None
        if selected:
            # Native app deep links can be scanned, but are never returned as
            # arbitrary browser navigation/external execution targets.
            if isinstance(selected, str) and selected.startswith(
                ("weixin:", "wxp:", "alipays:", "alipay:")
            ):
                qr_payload = self._public_ref(selected, qr=True)
            else:
                checkout_url = self._public_ref(selected, qr=False)
        if not checkout_url and not qr_payload:
            raise _error()
        return Checkout(
            provider_order_id=reference, checkout_url=checkout_url, qr_payload=qr_payload
        )

    def _echoes(self, result: dict, reference: str) -> None:
        for key, expected in (
            ("out_trade_no", reference),
            ("pid", self._config["pid"]),
            ("type", self._config["payment_type"]),
        ):
            echoed = _field(result, key)
            if echoed is not None and _scalar(echoed) != expected:
                raise _error()

    def _query(self, reference: str) -> PaymentEvidence:
        params = {
            "act": "order",
            "pid": self._config["pid"],
            "key": self._config["pkey"],
            "out_trade_no": reference,
        }
        result = self._request("api.php", query=params, form=params)
        code = _field(result, "code")
        if code is not None and _code(code) != 1:
            raise _error()
        self._echoes(result, reference)
        trade_status, status = _field(result, "trade_status"), _field(result, "status")
        if trade_status is None and status is None:
            raise _error()
        paid = (
            _scalar(trade_status) == "TRADE_SUCCESS"
            if trade_status is not None
            else _code(status) == 1
        )
        if trade_status is not None and status is not None and paid != (_code(status) == 1):
            raise _error()
        money, trade_no = _field(result, "money"), _field(result, "trade_no")
        amount = _money(money) if money is not None else None
        payment_id = _trade_id(trade_no) if trade_no is not None else None
        if paid and (amount is None or payment_id is None):
            raise _error()
        return PaymentEvidence(
            order_id=str(UUID(hex=reference)),
            state="paid" if paid else "pending",
            provider_order_id=reference,
            payment_id=payment_id,
            amount_minor=amount,
            currency="cny" if amount is not None else None,
        )

    def query_order(self, order: CheckoutRequest, provider_order_id: str | None) -> PaymentEvidence:
        reference = self._validate_order(order)
        if provider_order_id is not None and provider_order_id != reference:
            raise _error()
        evidence = self._query(reference)
        if evidence.amount_minor is not None and evidence.amount_minor != order.amount_minor:
            raise _error()
        return evidence

    def close_order(self, order: CheckoutRequest, provider_order_id: str) -> PaymentEvidence:
        # There is no portable authoritative close endpoint in classic V1.
        return self.query_order(order, provider_order_id)

    def verify_webhook(self, raw: bytes, headers: Mapping[str, str]) -> PaymentEvidence | None:
        content_type = {key.lower(): value for key, value in headers.items()}.get(
            "content-type", ""
        )
        if (
            content_type
            and content_type.split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded"
        ):
            raise ProviderError("易支付通知格式无效")
        try:
            if len(raw) > _MAX_BODY:
                raise ValueError
            text = raw.decode("utf-8")
            if re.search(r"%(?![0-9A-Fa-f]{2})", text):
                raise ValueError
            pairs = parse_qsl(
                text,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=20,
            )
            params = {}
            for key, value in pairs:
                if (
                    key in params
                    or key not in _NOTIFY_KEYS
                    or any(char in value for char in "\x00\r\n")
                    or (key != "name" and any(char in value for char in "&="))
                ):
                    raise ValueError
                params[key] = value
            sign = params.get("sign", "")
            if (
                params.get("sign_type") != "MD5"
                or not re.fullmatch(r"[0-9a-f]{32}", sign)
                or not hmac.compare_digest(_sign(params, self._config["pkey"]), sign)
                or params.get("pid") != self._config["pid"]
                or params.get("type") != self._config["payment_type"]
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise ProviderError("易支付通知验证失败") from None
        reference = params.get("out_trade_no", "")
        if not re.fullmatch(r"[0-9a-f]{32}", reference):
            raise _error()
        amount = _money(params.get("money"))
        payment_id = _trade_id(params.get("trade_no"))
        if params.get("trade_status") != "TRADE_SUCCESS":
            return None
        evidence = self._query(reference)
        if (
            evidence.state != "paid"
            or evidence.amount_minor != amount
            or evidence.payment_id != payment_id
        ):
            raise ProviderError("易支付通知尚未通过网关查单确认")
        return evidence

    @staticmethod
    def _definite_not_found(result: dict) -> bool:
        # Restrict fallback to complete, explicit rejection messages; a message
        # merely containing these words could still describe an uncertain state.
        message = _field(result, "msg")
        return isinstance(message, str) and message.strip().lower().rstrip(".!。！") in {
            "订单不存在",
            "订单编号不存在",
            "order not found",
            "order does not exist",
            "order not exist",
        }

    def create_refund(
        self, order: CheckoutRequest, payment_id: str, refund_id: str, amount_minor: int
    ) -> RefundEvidence:
        reference = self._validate_order(order)
        _uuid_hex(refund_id)
        payment_id = _trade_id(payment_id)
        if _minor_units(amount_minor) > order.amount_minor:
            raise _error()
        original = self.query_order(order, reference)
        if original.state != "paid" or original.payment_id != payment_id:
            raise _error()
        common = {
            "pid": self._config["pid"],
            "key": self._config["pkey"],
            "money": _yuan(amount_minor),
        }
        for index, identifier in enumerate(({"out_trade_no": reference}, {"trade_no": payment_id})):
            # No retry on transport/format/significance errors. Classic V1 does
            # not honor our refund UUID as an idempotency key.
            result = self._request(
                "api.php", query={"act": "refund"}, form={**common, **identifier}
            )
            self._echoes(result, reference)
            # Resolve every meaningful field before deciding on a retry or
            # completion. PHP forks may wrap results in data, and conflicting
            # top-level/nested values must never authorize a second refund.
            trade_no, money = _field(result, "trade_no"), _field(result, "money")
            statuses = [_field(result, field) for field in ("status", "refund_status")]
            code = _code(_field(result, "code"))
            not_found = self._definite_not_found(result)
            if trade_no is not None and _trade_id(trade_no) != payment_id:
                raise _error()
            if money is not None and _money(money) != amount_minor:
                raise _error()
            if code != self._refund_success:
                if index == 0 and not_found and all(status is None for status in statuses):
                    continue
                raise _error()
            if not_found:
                # A contradictory configured success code is not safe evidence.
                raise _error()
            for status in statuses:
                if status is not None and _scalar(status) not in (
                    "1",
                    "SUCCESS",
                    "REFUND_SUCCESS",
                    "success",
                    "succeeded",
                ):
                    raise _error()
            return RefundEvidence(state="succeeded", amount_minor=amount_minor, currency="cny")
        raise _error()

    def query_refund(
        self,
        order: CheckoutRequest,
        payment_id: str,
        refund_id: str,
        provider_refund_id: str | None,
    ) -> RefundEvidence:
        self._validate_order(order)
        _trade_id(payment_id)
        _uuid_hex(refund_id)
        raise ProviderError("此易支付 V1 渠道不支持可靠的单次退款查询，请管理员核对渠道流水")
