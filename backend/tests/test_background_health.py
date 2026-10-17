"""Thread health checks do not run models or contact media services."""
import importlib
import threading

import pytest

from app.core import health


def test_disabled_worker_is_not_reported_as_broken(monkeypatch):
    monkeypatch.setattr(health.settings, "library_worker_enabled", False)
    monkeypatch.setattr(health, "background_workers", {})
    assert health.background_status()["library"] == "disabled"


def test_enabled_missing_worker_is_unavailable(monkeypatch):
    monkeypatch.setattr(health.settings, "library_worker_enabled", True)
    monkeypatch.setattr(health, "background_workers", {})
    assert health.background_status()["library"] == "unavailable"


@pytest.mark.parametrize("module", [
    "library.worker", "library.processing_worker", "transcription.worker",
    "agent.worker", "canvas.worker",
])
def test_health_tracks_actual_worker_lifecycle(module, monkeypatch):
    worker = importlib.import_module("app.modules." + module)
    entered = threading.Event()
    def idle(*args, **kwargs):
        entered.set()
        return False
    monkeypatch.setattr(worker, "run_once", idle)
    stop = worker.start()
    try:
        assert entered.wait(3)
        monkeypatch.setattr(health.settings, "library_worker_enabled", True)
        monkeypatch.setattr(health, "background_workers", {"library": stop.is_alive})
        assert health.background_status()["library"] == "ok"
    finally:
        stop()
    assert health.background_status()["library"] == "unavailable"
