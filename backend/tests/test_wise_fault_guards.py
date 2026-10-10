"""Pure guard tests, NOT evidence of a real WISE cancellation or hard kill."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.wise_fault_support import assert_idle, require_window


def test_fault_requires_fresh_explicit_window_and_exact_container():
    now = datetime(2026, 10, 4, tzinfo=UTC)
    environment = {
        "INSPIRATION_WISE_EXCLUSIVE_ACK": "non-test-work-is-idle",
        "INSPIRATION_WISE_EXCLUSIVE_UNTIL": (now + timedelta(minutes=30)).isoformat(),
        "INSPIRATION_WISE_FAULT_CONTAINER_ID": "a" * 64,
    }
    require_window(environment, "a" * 64, now=now)
    for changes in (
        {"INSPIRATION_WISE_EXCLUSIVE_ACK": ""},
        {"INSPIRATION_WISE_FAULT_CONTAINER_ID": "different"},
        {"INSPIRATION_WISE_EXCLUSIVE_UNTIL": (now - timedelta(seconds=1)).isoformat()},
        {"INSPIRATION_WISE_EXCLUSIVE_UNTIL": (now + timedelta(hours=3)).isoformat()},
        {"INSPIRATION_WISE_EXCLUSIVE_UNTIL": "2026-10-04T00:30:00"},
    ):
        with pytest.raises(AssertionError):
            require_window(environment | changes, "a" * 64, now=now)


def test_idle_guard_rejects_foreign_remote_main_and_cms_work():
    own = {"active_jobs": [{"project_id": "own"}]}
    main, cms = {"processing": 0, "uploads": 0}, {"media": 0, "active": 0}
    assert_idle(own, main, cms, allowed_project="own")
    for remote, application, external in (
        ({"active_jobs": [{"project_id": "other"}]}, main, cms),
        (own, main | {"processing": 1}, cms),
        (own, main, cms | {"media": 1}),
        (own, main, cms | {"active": 1}),
        (own, {}, cms),
    ):
        with pytest.raises(AssertionError):
            assert_idle(remote, application, external, allowed_project="own")
