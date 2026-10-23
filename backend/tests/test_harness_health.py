"""Production readiness must not inherit the legacy agent worker's liveness."""

from app.core import health


def test_production_health_checks_its_own_worker(monkeypatch):
    monkeypatch.setattr(health.settings, "agent_worker_enabled", True)
    monkeypatch.setattr(health, "background_workers", {"agent": lambda: True})
    assert health.background_status()["agent"] == "ok"
    assert health.background_status()["production"] == "unavailable"
    health.background_workers["production"] = lambda: True
    assert health.background_status()["production"] == "ok"
    health.background_workers["production"] = lambda: False
    assert health.background_status()["production"] == "unavailable"
    monkeypatch.setattr(health.settings, "agent_worker_enabled", False)
    assert health.background_status()["production"] == "disabled"
