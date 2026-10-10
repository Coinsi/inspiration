"""Optional readiness must not block base ingestion; isolated PostgreSQL only."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.library import MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.modules.library import optional_capabilities, processing
from tests.test_library import library, media_studio  # noqa: F401
from tests.test_media_processing import setup
from tests.test_media_processing_queue import configure_auto, new_run


def readiness(ready=False, reason="model_validation_required"):
    return {
        "object": {"configured": True, "ready": ready, "reason": None if ready else reason},
        "face": {"configured": True, "ready": False, "reason": "license_record_missing"},
    }


@pytest.mark.parametrize("reason", ["model_validation_required", "service_unavailable"])
def test_unready_optional_keeps_base_ingestion_and_visible_reason(library, monkeypatch, reason):
    _, sessions, _, _, _ = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    monkeypatch.setattr(
        settings, "library_processing_wise_auto_capabilities", ["visual", "audio", "face", "object"]
    )
    monkeypatch.setattr(
        settings, "library_wise_capabilities", ["visual", "audio", "face", "object"]
    )
    monkeypatch.setattr(settings, "library_mediacms_transcription_enabled", True)
    monkeypatch.setattr(
        optional_capabilities, "optional_states", lambda **_: readiness(reason=reason)
    )
    with sessions() as db:
        assert processing.enqueue_automatic(db)
        assert processing.enqueue_automatic(db)
        assert processing.enqueue_automatic(db) is False
        rows = {row.provider: row for row in db.scalars(select(MediaProcessingRun))}
        assert rows["wise"].capabilities == ["audio", "visual"]
        assert rows["mediacms"].capabilities == ["preview", "speech"]
        assert all(row.status == "queued" for row in rows.values())
    state = processing.providers()["providers"][0]
    assert state["capabilities"] == ["visual", "audio", "face", "object"]
    assert state["optional_models"]["object"] == readiness(reason=reason)["object"]


def test_verified_object_is_queued_with_base_capabilities(library, monkeypatch):
    _, sessions, _, _, _ = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    monkeypatch.setattr(
        settings, "library_processing_wise_auto_capabilities", ["visual", "audio", "object"]
    )
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "audio", "object"])
    monkeypatch.setattr(optional_capabilities, "optional_states", lambda **_: readiness(ready=True))
    with sessions() as db:
        assert processing.enqueue_automatic(db)
        row = db.scalar(select(MediaProcessingRun))
        assert row.provider == "wise" and row.capabilities == ["audio", "object", "visual"]


@pytest.mark.parametrize("base_enabled", [True, False])
def test_optional_expires_between_scan_and_submit_without_blocking_other_work(
    library, monkeypatch, base_enabled
):
    _, sessions, _, _, _ = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    monkeypatch.setattr(
        settings, "library_processing_wise_auto_capabilities", ["visual", "audio", "object"]
    )
    monkeypatch.setattr(
        settings,
        "library_wise_capabilities",
        ["visual", "audio", "object"] if base_enabled else ["object"],
    )
    calls = []

    def states(*, refresh=False):
        calls.append(refresh)
        return readiness(ready=not refresh, reason="service_unavailable")

    monkeypatch.setattr(optional_capabilities, "optional_states", states)
    with sessions() as db:
        assert processing.enqueue_automatic(db)
        if base_enabled:
            assert processing.enqueue_automatic(db)
        assert processing.enqueue_automatic(db) is False
        rows = {row.provider: row for row in db.scalars(select(MediaProcessingRun))}
        assert set(rows) == ({"wise", "mediacms"} if base_enabled else {"mediacms"})
        if base_enabled:
            assert rows["wise"].capabilities == ["audio", "visual"]
        assert rows["mediacms"].capabilities == ["preview"]
        assert all(row.status == "queued" for row in rows.values())
    assert True in calls  # submit performs its own fresh check, not just cached readiness


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_new_optional_readiness_does_not_restart_historical_run(library, monkeypatch, status):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    monkeypatch.setattr(
        settings, "library_processing_wise_auto_capabilities", ["visual", "audio", "object"]
    )
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "audio", "object"])
    monkeypatch.setattr(optional_capabilities, "optional_states", lambda **_: readiness(ready=True))
    with sessions() as db:
        version = db.get(MediaVersion, uuid.UUID(info["id"]))
        existing = new_run(db, version, ids["user"], provider="wise", status=status)
        db.commit()
        assert processing.enqueue_automatic(db)  # MediaCMS is still admitted
        assert processing.enqueue_automatic(db) is False
        rows = list(db.scalars(select(MediaProcessingRun)))
        assert len(rows) == 2
        db.refresh(existing)
        assert existing.status == status and existing.capabilities == ["visual"]


def test_optional_stays_on_demand_without_explicit_auto_opt_in(library, monkeypatch):
    from app.core.config import Settings

    _, sessions, _, _, _ = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    defaults = Settings.model_fields["library_processing_wise_auto_capabilities"].default
    assert defaults == ["visual", "audio"]
    monkeypatch.setattr(settings, "library_processing_wise_auto_capabilities", defaults)
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "audio", "object"])
    monkeypatch.setattr(
        optional_capabilities,
        "optional_states",
        lambda **_: pytest.fail("On-demand optional models do not need an automatic health check"),
    )
    with sessions() as db:
        assert processing.enqueue_automatic(db)
        row = db.scalar(select(MediaProcessingRun))
        assert row.provider == "wise" and row.capabilities == ["audio", "visual"]


def test_auto_capabilities_cannot_enable_disabled_models(library, monkeypatch):
    _, sessions, _, _, _ = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    monkeypatch.setattr(settings, "library_processing_wise_auto_capabilities", ["object"])
    with sessions() as db:
        assert processing.enqueue_automatic(db)
        assert processing.enqueue_automatic(db) is False
        row = db.scalar(select(MediaProcessingRun))
        assert row.provider == "mediacms" and row.capabilities == ["preview"]
