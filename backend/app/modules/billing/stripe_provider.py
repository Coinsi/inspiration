"""Direct Stripe Checkout integration; no user-controlled gateway URLs.

Protocol references (reviewed 2026-11-22):
https://docs.stripe.com/api/checkout/sessions/create
https://docs.stripe.com/api/checkout/sessions/expire
https://docs.stripe.com/api/idempotent_requests
https://docs.stripe.com/events/manage-webhook-endpoints
https://docs.stripe.com/api/refunds/list
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .providers import Checkout, CheckoutRequest, PaymentEvidence, ProviderError, RefundEvidence

_API = "https://api.stripe.com"
_VERSION = "2026-11-18.endive"
_MAX_PAGES = 10
_MAX_WEBHOOK_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024


def validate_stripe_config(config: dict[str, str]) -> None:
    if not isinstance(config, dict) or set(config) - {"secret_key", "webhook_secret"}:
        raise ProviderError("Invalid Stripe configuration")
    for key, pattern in (
        ("secret_key", r"(?:sk|rk)_(?:test|live)_[A-Za-z0-9]+"),
        ("webhook_secret", r"whsec_[A-Za-z0-9]+"),
    ):
        value = config.get(key)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ProviderError("Invalid Stripe configuration")


def _uuid(value: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ProviderError("Invalid payment reference") from None
    return value


def _identifier(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9_]{1,200}", value):
        raise ProviderError("Invalid Stripe object reference")
    return value


def _money(value: Any) -> int:
    if type(value) is not int or not 0 < value <= 99_999_999:
        raise ProviderError("Invalid Stripe amount")
    return value


def _currency(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z]{3}", value):
        raise ProviderError("Invalid Stripe currency")
    return value


def _metadata(obj: dict[str, Any]) -> dict[str, Any]:
    value = obj.get("metadata")
    if not isinstance(value, dict):
        raise ProviderError("Missing Stripe payment identity")
    return value


def _secure_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        if parsed.port not in (None, 443) or any(ord(ch) < 32 for ch in value):
            raise ValueError
    except (ValueError, TypeError):
        raise ProviderError("Invalid payment return URL") from None


class StripeProvider:
    def __init__(self, config: dict[str, str]):
        validate_stripe_config(config)
        self._secret = config["secret_key"]
        self._webhook_secret = config["webhook_secret"]
        self._livemode = "_live_" in self._secret

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": "Bearer " + self._secret,
            "Stripe-Version": _VERSION,
            "Accept-Encoding": "identity",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(
                timeout=httpx.Timeout(15, connect=5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    method,
                    _API + path,
                    data=data,
                    params=params,
                    headers=headers,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        # Do not expose provider errors, submitted metadata, or credentials.
                        raise ProviderError("Stripe request could not be confirmed")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ProviderError("Unexpected Stripe response encoding")
                    length = response.headers.get("content-length")
                    if length is not None and not 0 <= int(length) <= _MAX_RESPONSE_BYTES:
                        raise ProviderError("Stripe response exceeds the size limit")
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        if len(raw) + len(chunk) > _MAX_RESPONSE_BYTES:
                            raise ProviderError("Stripe response exceeds the size limit")
                        raw.extend(chunk)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ProviderError("Invalid Stripe response")
            return result
        except (httpx.HTTPError, ValueError, RecursionError):
            raise ProviderError("Stripe request could not be confirmed") from None

    def _validate_order(self, order: CheckoutRequest) -> None:
        _uuid(order.order_id)
        _money(order.amount_minor)
        _currency(order.currency)
        if order.expires_at.tzinfo is None:
            raise ProviderError("Payment expiry must include a timezone")

    def _mode(self, obj: dict[str, Any]) -> None:
        if obj.get("livemode") is not self._livemode:
            raise ProviderError("Stripe account mode mismatch")

    def _session_evidence(self, obj: dict[str, Any]) -> PaymentEvidence:
        self._mode(obj)
        if obj.get("object") != "checkout.session" or obj.get("mode") != "payment":
            raise ProviderError("Invalid Stripe Checkout response")
        order_id = _uuid(_metadata(obj).get("order_id"))
        if obj.get("client_reference_id") != order_id:
            raise ProviderError("Stripe payment identity mismatch")
        provider_id = _identifier(obj.get("id"), "cs_")
        amount = _money(obj.get("amount_total"))
        currency = _currency(obj.get("currency"))
        status, payment_status = obj.get("status"), obj.get("payment_status")
        if not isinstance(status, str) or status not in {"open", "complete", "expired"}:
            raise ProviderError("Unknown Stripe Checkout state")
        if not isinstance(payment_status, str) or payment_status not in {
            "paid",
            "unpaid",
            "no_payment_required",
        }:
            raise ProviderError("Unknown Stripe payment state")
        state = "pending"
        payment_id = None
        if payment_status == "paid":
            if status != "complete":
                raise ProviderError("Inconsistent Stripe payment state")
            payment_id = _identifier(obj.get("payment_intent"), "pi_")
            state = "paid"
        elif status == "expired":
            state = "expired"
        return PaymentEvidence(
            order_id=order_id,
            state=state,
            provider_order_id=provider_id,
            payment_id=payment_id,
            amount_minor=amount,
            currency=currency,
        )

    def _match_order(self, order: CheckoutRequest, evidence: PaymentEvidence) -> None:
        if (
            evidence.order_id != order.order_id
            or evidence.amount_minor != order.amount_minor
            or evidence.currency != order.currency
        ):
            raise ProviderError("Stripe payment does not match the order")

    def create_checkout(self, order: CheckoutRequest) -> Checkout:
        self._validate_order(order)
        _secure_url(order.success_url)
        _secure_url(order.cancel_url)
        expires = int(order.expires_at.timestamp())
        if not 1800 <= expires - int(time.time()) <= 86400:
            raise ProviderError("Stripe Checkout expiry must be 30 minutes to 24 hours away")
        result = self._request(
            "POST",
            "/v1/checkout/sessions",
            data={
                "mode": "payment",
                "payment_method_types[0]": "card",
                "adaptive_pricing[enabled]": "false",
                "client_reference_id": order.order_id,
                "metadata[order_id]": order.order_id,
                "payment_intent_data[metadata][order_id]": order.order_id,
                "line_items[0][price_data][currency]": order.currency,
                "line_items[0][price_data][unit_amount]": str(order.amount_minor),
                "line_items[0][price_data][product_data][name]": order.title,
                "line_items[0][quantity]": "1",
                "success_url": order.success_url,
                "cancel_url": order.cancel_url,
                "expires_at": str(expires),
            },
            idempotency_key="inspiration-checkout-" + order.order_id,
        )
        evidence = self._session_evidence(result)
        self._match_order(order, evidence)
        checkout_url = result.get("url")
        if not isinstance(checkout_url, str):
            raise ProviderError("Stripe Checkout URL unavailable; query the order")
        _secure_url(checkout_url)
        if urlsplit(checkout_url).hostname != "checkout.stripe.com":
            raise ProviderError("Untrusted Stripe Checkout URL")
        return Checkout(provider_order_id=evidence.provider_order_id, checkout_url=checkout_url)

    def _list(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        items = []
        query = {**params, "limit": "100"}
        seen = set()
        for _ in range(_MAX_PAGES):
            result = self._request("GET", path, params=query)
            page = result.get("data")
            if (
                result.get("object") != "list"
                or not isinstance(page, list)
                or any(not isinstance(item, dict) for item in page)
                or type(result.get("has_more")) is not bool
            ):
                raise ProviderError("Invalid Stripe list response")
            items.extend(page)
            if not result["has_more"]:
                return items
            cursor = page[-1].get("id") if page else None
            if not isinstance(cursor, str) or cursor in seen:
                raise ProviderError("Incomplete Stripe reconciliation")
            seen.add(cursor)
            query["starting_after"] = cursor
        raise ProviderError("Stripe reconciliation requires further review")

    def query_order(self, order: CheckoutRequest, provider_order_id: str | None) -> PaymentEvidence:
        self._validate_order(order)
        if provider_order_id:
            provider_order_id = _identifier(provider_order_id, "cs_")
            result = self._request("GET", "/v1/checkout/sessions/" + provider_order_id)
            if result.get("id") != provider_order_id:
                raise ProviderError("Stripe Checkout identity mismatch")
        else:
            # A timed-out create can lose the session ID. Bound recovery to the
            # immutable expiry snapshot and Stripe's maximum session lifetime.
            end = int(order.expires_at.timestamp())
            matches = [
                item
                for item in self._list(
                    "/v1/checkout/sessions",
                    {
                        "created[gte]": str(end - 86400),
                        "created[lte]": str(end),
                    },
                )
                if isinstance(item.get("metadata"), dict)
                and item["metadata"].get("order_id") == order.order_id
            ]
            if len(matches) > 1:
                raise ProviderError("Multiple Stripe sessions require review")
            if not matches:
                return PaymentEvidence(order_id=order.order_id, state="pending")
            result = matches[0]
        evidence = self._session_evidence(result)
        self._match_order(order, evidence)
        return evidence

    def close_order(self, order: CheckoutRequest, provider_order_id: str) -> PaymentEvidence:
        provider_order_id = _identifier(provider_order_id, "cs_")
        original = self.query_order(order, provider_order_id)
        if original.state in ("paid", "expired"):
            return original
        try:
            result = self._request(
                "POST",
                "/v1/checkout/sessions/" + provider_order_id + "/expire",
                idempotency_key="inspiration-expire-" + order.order_id,
            )
            if result.get("id") != provider_order_id:
                raise ProviderError("Stripe Checkout identity mismatch")
            evidence = self._session_evidence(result)
            self._match_order(order, evidence)
            if evidence.state in ("paid", "expired"):
                return evidence
        except ProviderError:
            # Payment can win the race with expiry. A timeout or rejected expiry
            # is not a canceled order; confirm the original session again.
            pass
        return self.query_order(order, provider_order_id)

    def verify_webhook(self, raw: bytes, headers: Mapping[str, str]) -> PaymentEvidence | None:
        if len(raw) > _MAX_WEBHOOK_BYTES:
            raise ProviderError("Invalid Stripe notification")
        header = {key.lower(): value for key, value in headers.items()}.get("stripe-signature", "")
        parts: dict[str, list[str]] = {}
        for part in header.split(","):
            key, sep, value = part.strip().partition("=")
            if sep:
                parts.setdefault(key, []).append(value)
        timestamps = parts.get("t", [])
        if len(timestamps) != 1 or not re.fullmatch(r"[0-9]{1,12}", timestamps[0]):
            raise ProviderError("Invalid Stripe signature")
        timestamp = timestamps[0]
        if abs(time.time() - int(timestamp)) > 300:
            raise ProviderError("Expired Stripe signature")
        expected = hmac.new(
            self._webhook_secret.encode(),
            timestamp.encode() + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        if not any(
            re.fullmatch(r"[a-f0-9]{64}", value) and hmac.compare_digest(expected, value)
            for value in parts.get("v1", [])
        ):
            raise ProviderError("Invalid Stripe signature")
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ProviderError("Invalid Stripe notification") from None
        if not isinstance(event, dict) or event.get("object") != "event":
            raise ProviderError("Invalid Stripe notification")
        self._mode(event)
        # This direct-merchant adapter never accepts Connect events.
        if event.get("account"):
            raise ProviderError("Unexpected Stripe connected account")
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise ProviderError("Invalid Stripe event type")
        if event_type not in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
            "checkout.session.expired",
        }:
            return None
        data = event.get("data")
        obj = data.get("object") if isinstance(data, dict) else None
        if not isinstance(obj, dict):
            raise ProviderError("Invalid Stripe notification")
        return self._session_evidence(obj)

    def _validate_payment(self, order: CheckoutRequest, payment_id: str) -> None:
        self._validate_order(order)
        _identifier(payment_id, "pi_")
        obj = self._request("GET", "/v1/payment_intents/" + payment_id)
        self._mode(obj)
        if (
            obj.get("object") != "payment_intent"
            or obj.get("id") != payment_id
            or _metadata(obj).get("order_id") != order.order_id
            or obj.get("status") != "succeeded"
            or _money(obj.get("amount_received")) != order.amount_minor
            or _currency(obj.get("currency")) != order.currency
        ):
            raise ProviderError("Stripe payment does not match the refund order")

    def _refund_evidence(
        self,
        obj: dict[str, Any],
        order: CheckoutRequest,
        payment_id: str,
        refund_id: str,
    ) -> RefundEvidence:
        metadata = _metadata(obj)
        if (
            obj.get("object") != "refund"
            or obj.get("payment_intent") != payment_id
            or metadata.get("order_id") != order.order_id
            or metadata.get("refund_id") != refund_id
        ):
            raise ProviderError("Stripe refund identity mismatch")
        amount = _money(obj.get("amount"))
        currency = _currency(obj.get("currency"))
        if amount > order.amount_minor or currency != order.currency:
            raise ProviderError("Stripe refund amount mismatch")
        status = obj.get("status")
        if not isinstance(status, str):
            raise ProviderError("Unknown Stripe refund state")
        if status == "succeeded":
            state = "succeeded"
        elif status in {"failed", "canceled"}:
            state = "failed"
        elif status in {"pending", "requires_action"}:
            state = "pending"
        else:
            raise ProviderError("Unknown Stripe refund state")
        return RefundEvidence(
            state=state,
            provider_refund_id=_identifier(obj.get("id"), "re_"),
            amount_minor=amount,
            currency=currency,
        )

    def _find_refund(self, payment_id: str, refund_id: str) -> dict[str, Any] | None:
        matches = [
            item
            for item in self._list("/v1/refunds", {"payment_intent": payment_id})
            if isinstance(item.get("metadata"), dict)
            and item["metadata"].get("refund_id") == refund_id
        ]
        if len(matches) > 1:
            raise ProviderError("Multiple Stripe refunds require review")
        return matches[0] if matches else None

    def create_refund(
        self,
        order: CheckoutRequest,
        payment_id: str,
        refund_id: str,
        amount_minor: int,
    ) -> RefundEvidence:
        _uuid(refund_id)
        _money(amount_minor)
        if amount_minor > order.amount_minor:
            raise ProviderError("Invalid refund amount")
        self._validate_payment(order, payment_id)
        # Keys can be pruned after 24h: also recover by metadata before POST.
        result = self._find_refund(payment_id, refund_id)
        if result is None:
            result = self._request(
                "POST",
                "/v1/refunds",
                data={
                    "payment_intent": payment_id,
                    "amount": str(amount_minor),
                    "metadata[order_id]": order.order_id,
                    "metadata[refund_id]": refund_id,
                },
                idempotency_key="inspiration-refund-" + refund_id,
            )
        evidence = self._refund_evidence(result, order, payment_id, refund_id)
        if evidence.amount_minor != amount_minor:
            raise ProviderError("Stripe refund amount mismatch")
        return evidence

    def query_refund(
        self,
        order: CheckoutRequest,
        payment_id: str,
        refund_id: str,
        provider_refund_id: str | None,
    ) -> RefundEvidence:
        _uuid(refund_id)
        self._validate_payment(order, payment_id)
        if provider_refund_id:
            _identifier(provider_refund_id, "re_")
            result = self._request("GET", "/v1/refunds/" + provider_refund_id)
            if result.get("id") != provider_refund_id:
                raise ProviderError("Stripe refund identity mismatch")
        else:
            result = self._find_refund(payment_id, refund_id)
            if result is None:
                # Absence is not proof of a declined refund after a timeout.
                return RefundEvidence(state="pending")
        return self._refund_evidence(result, order, payment_id, refund_id)
