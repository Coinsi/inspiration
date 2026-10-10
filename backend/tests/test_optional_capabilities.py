"""A configured optional model never substitutes for verified inference."""

from pathlib import Path

import pytest

from app.core.config import settings
from app.core.errors import CapabilityUnsupported
from app.modules.library import optional_capabilities as optional
from app.modules.library.providers.wise import WiseClient


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_wise_url", "http://private-wise.test")
    monkeypatch.setattr(settings, "library_wise_token", "private-test-token")
    monkeypatch.setattr(
        settings, "library_wise_capabilities", ["visual", "audio", "face", "object"]
    )
    optional._cache.clear()


def test_default_disabled_does_not_call_service(monkeypatch):
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "audio"])
    monkeypatch.setattr(WiseClient, "health", lambda _: pytest.fail("Unneeded service request"))
    states = optional.optional_states()
    assert all(state["reason"] == "capability_disabled" for state in states.values())
    optional.require_optional(["visual", "audio"])


@pytest.mark.parametrize(
    "reason",
    [
        "model_manifest_missing",
        "model_file_missing",
        "license_record_missing",
        "model_validation_required",
    ],
)
def test_private_reason_codes_are_sanitized_and_cached(configured, monkeypatch, reason):
    calls = []

    def health(_):
        calls.append(True)
        return {
            "service": "inspiration-wise-bridge",
            "capabilities": {
                cap: {
                    "configured": True,
                    "ready": False,
                    "reason": reason,
                    "private_path": "/secret",
                }
                for cap in optional.OPTIONAL
            },
        }

    monkeypatch.setattr(WiseClient, "health", health)
    first = optional.optional_states()
    assert first == optional.optional_states() and len(calls) == 1
    assert all(row["reason"] == reason for row in first.values())
    assert "secret" not in str(first) and "private-test-token" not in str(first)
    with pytest.raises(CapabilityUnsupported) as error:
        optional.require_optional(["object"])
    assert error.value.detail["reason"] == reason and len(calls) == 2


def test_invented_ready_flag_cannot_enable_unaccepted_model(configured, monkeypatch):
    monkeypatch.setattr(
        WiseClient,
        "health",
        lambda _: {
            "service": "inspiration-wise-bridge",
            "capabilities": {"object": {"ready": True, "availability": "ready"}},
        },
    )
    assert optional.optional_states()["object"]["ready"] is False
    with pytest.raises(CapabilityUnsupported):
        optional.require_optional(["object"])


def test_verified_private_receipt_protocol_can_enable_object_only(configured, monkeypatch):
    receipt = {
        "configured": True,
        "ready": True,
        "availability": "ready",
        "integrity_verified": True,
        "validation": {
            "protocol": "wise-optional-probe-v1",
            "receipt_digest": "a" * 64,
            "implementation_digest": "b" * 64,
        },
    }
    monkeypatch.setattr(
        WiseClient,
        "health",
        lambda _: {
            "service": "inspiration-wise-bridge",
            "capabilities": {cap: receipt for cap in optional.OPTIONAL},
        },
    )
    state = optional.optional_states()
    assert state["object"]["ready"] is True and state["object"]["reason"] is None
    assert state["face"]["ready"] is False
    optional.require_optional(["object"])
    receipt["validation"]["protocol"] = "unsupported"
    assert optional.optional_states(refresh=True)["object"]["ready"] is False


def test_failed_or_wrong_service_returns_safe_unavailable(configured, monkeypatch):
    monkeypatch.setattr(
        WiseClient, "health", lambda _: {"service": "wrong", "detail": "private data"}
    )
    assert optional.optional_states()["face"]["reason"] == "service_unavailable"


def test_old_queued_optional_upload_is_rejected_before_opening_source(configured, monkeypatch):
    monkeypatch.setattr(
        WiseClient, "health", lambda _: {"service": "inspiration-wise-bridge", "capabilities": {}}
    )
    monkeypatch.setattr(Path, "open", lambda *_: pytest.fail("No media transfer before readiness"))
    with pytest.raises(CapabilityUnsupported):
        WiseClient("http://private-wise.test", "test").submit(
            None, None, None, Path("unused"), ["face"]
        )
