"""Payment provider contracts. All monetary values are integer minor units.

Providers receive immutable order/config snapshots from the billing service.
Evidence is authenticated provider data, not permission to credit: the service
must also compare the saved order, amount, currency and payment identity.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.core.errors import ProviderError

__all__ = [
    "Checkout",
    "CheckoutRequest",
    "PaymentEvidence",
    "Provider",
    "ProviderError",
    "RefundEvidence",
    "get_provider",
    "provider_catalog",
    "validate_provider_config",
]


@dataclass(frozen=True)
class CheckoutRequest:
    order_id: str
    amount_minor: int
    currency: str
    title: str
    success_url: str
    cancel_url: str
    webhook_url: str
    expires_at: datetime
    client_ip: str | None = None
    is_mobile: bool = False


@dataclass(frozen=True)
class Checkout:
    provider_order_id: str
    checkout_url: str | None = None
    qr_payload: str | None = None


@dataclass(frozen=True)
class PaymentEvidence:
    order_id: str
    state: Literal["pending", "paid", "expired"]
    provider_order_id: str | None = None
    payment_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None


@dataclass(frozen=True)
class RefundEvidence:
    state: Literal["pending", "succeeded", "failed"]
    provider_refund_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None


class Provider(Protocol):
    def create_checkout(self, order: CheckoutRequest) -> Checkout: ...

    def query_order(
        self, order: CheckoutRequest, provider_order_id: str | None
    ) -> PaymentEvidence: ...

    def close_order(self, order: CheckoutRequest, provider_order_id: str) -> PaymentEvidence: ...

    def verify_webhook(self, raw: bytes, headers: Mapping[str, str]) -> PaymentEvidence | None: ...

    def create_refund(
        self, order: CheckoutRequest, payment_id: str, refund_id: str, amount_minor: int
    ) -> RefundEvidence: ...

    def query_refund(
        self,
        order: CheckoutRequest,
        payment_id: str,
        refund_id: str,
        provider_refund_id: str | None,
    ) -> RefundEvidence: ...


def provider_catalog() -> list[dict]:
    def field(key: str, label: str, secret: bool = False, *, required: bool = True, **options):
        return {"key": key, "label": label, "secret": secret, "required": required, **options}

    return [
        {
            "kind": "stripe",
            "name": "Stripe Checkout",
            "fields": [
                field("secret_key", "Secret API key", True),
                field("webhook_secret", "Webhook signing secret", True),
            ],
        },
        {
            "kind": "alipay",
            "name": "支付宝电脑网站支付",
            "fields": [
                field("app_id", "应用 ID"),
                field("seller_id", "收款商户 UID"),
                field("app_private_key", "应用私钥 PEM", True),
                field("alipay_public_key", "支付宝公钥 PEM"),
            ],
        },
        {
            "kind": "wechatpay",
            "name": "微信支付 Native",
            "fields": [
                field("app_id", "App ID"),
                field("mch_id", "商户号"),
                field("merchant_serial", "商户 API 证书序列号"),
                field("merchant_private_key", "商户 API 私钥 PEM", True),
                field("api_v3_key", "API v3 密钥", True),
                field("platform_public_key_id", "微信支付公钥 ID"),
                field("platform_public_key", "微信支付公钥 PEM"),
            ],
        },
        {
            "kind": "easypay",
            "name": "易支付",
            "fields": [
                field("api_base", "网关 HTTPS 地址"),
                field("pid", "商户 ID（PID）"),
                field("pkey", "商户密钥（KEY）", True),
                field(
                    "payment_type",
                    "支付方式",
                    default="alipay",
                    options=[
                        {"value": "alipay", "label": "支付宝"},
                        {"value": "wxpay", "label": "微信支付"},
                    ],
                ),
                field(
                    "payment_mode",
                    "付款入口",
                    default="qrcode",
                    options=[
                        {"value": "qrcode", "label": "接口下单（二维码 / 跳转）"},
                        {"value": "popup", "label": "跳转易支付收银台"},
                    ],
                ),
                field("channel_id", "指定通道 ID", required=False),
                field(
                    "refund_success_code",
                    "退款成功返回码",
                    required=False,
                    default="1",
                    options=[
                        {"value": "1", "label": "1（易支付默认）"},
                        {"value": "0", "label": "0（按服务商文档）"},
                    ],
                ),
            ],
        },
    ]


def validate_provider_config(kind: str, config: dict[str, str]) -> None:
    catalog = next((item for item in provider_catalog() if item["kind"] == kind), None)
    if catalog is None:
        raise ProviderError("不支持此支付渠道")
    keys = {field["key"] for field in catalog["fields"]}
    required = {field["key"] for field in catalog["fields"] if field["required"]}
    if not isinstance(config, dict) or not required <= set(config) <= keys:
        raise ProviderError("支付渠道配置字段不完整或包含未知字段")
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 16384
        for value in config.values()
    ):
        raise ProviderError("支付渠道配置格式无效")
    if kind == "stripe":
        from .stripe_provider import validate_stripe_config

        validate_stripe_config(config)
    elif kind == "alipay":
        from .alipay_provider import validate_alipay_config

        validate_alipay_config(config)
    elif kind == "easypay":
        from .easypay_provider import validate_easypay_config

        validate_easypay_config(config)
    else:
        from .wechatpay_provider import validate_wechatpay_config

        validate_wechatpay_config(config)


def get_provider(kind: str, config: dict[str, str]) -> Provider:
    validate_provider_config(kind, config)
    if kind == "stripe":
        from .stripe_provider import StripeProvider

        return StripeProvider(config)
    if kind == "alipay":
        from .alipay_provider import AlipayProvider

        return AlipayProvider(config)
    if kind == "easypay":
        from .easypay_provider import EasyPayProvider

        return EasyPayProvider(config)
    from .wechatpay_provider import WeChatPayProvider

    return WeChatPayProvider(config)
