"""Real local stream probes and PostgreSQL admission; no remote model calls."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import inspect, select, text

from alembic import command
from app.core.config import settings
from app.models.identity import Membership
from app.models.library import MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.modules.generation import media_engine
from app.modules.library import processing
from tests.test_library import library, media_studio, upload  # noqa: F401
from tests.test_library_migrations import (  # noqa: F401
    library_migration_schema,
    seed_legacy_library,
)
from tests.test_media_processing_queue import configure_auto
from tests.test_media_workflow import post_ok


def silent_copy(content, tmp_path):
    source, silent = tmp_path / "source-audio.mp4", tmp_path / "silent.mp4"
    source.write_bytes(content)
    media_engine.run(["-i", str(source), "-map", "0:v:0", "-an", "-c:v", "copy", str(silent)])
    return silent.read_bytes()


@pytest.mark.parametrize("has_audio", [False, True])
def test_real_probe_auto_capabilities_and_reuse_keep_audio_presence(
    library, monkeypatch, tmp_path, has_audio
):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    version = upload(client, base, content if has_audio else silent_copy(content, tmp_path))
    assert version["has_audio"] is has_audio
    configure_auto(monkeypatch)
    monkeypatch.setattr(settings, "library_mediacms_transcription_enabled", True)
    if not has_audio:
        # Explicit choices are rejected as a whole, never silently reduced.
        for provider, capabilities in (
            ("wise", ["audio"]),
            ("wise", ["visual", "audio"]),
            ("mediacms", ["speech"]),
            ("mediacms", ["preview", "speech"]),
        ):
            response = client.post(
                f"{base}/versions/{version['id']}/process",
                json={
                    "provider": provider,
                    "capabilities": capabilities,
                },
            )
            assert response.status_code == 422, response.text
            error = response.json()["error"]
            assert error["detail"] == {"reason": "no_audio_track", "retryable": False}
            assert "没有音轨" in error["message"]
        with sessions() as db:
            assert list(db.scalars(select(MediaProcessingRun))) == []
    with sessions() as db:
        assert processing.enqueue_automatic(db)
        assert processing.enqueue_automatic(db)
        assert processing.enqueue_automatic(db) is False
        runs = {row.provider: row for row in db.scalars(select(MediaProcessingRun))}
        assert runs["wise"].capabilities == (["audio", "visual"] if has_audio else ["visual"])
        assert runs["mediacms"].capabilities == (
            ["preview", "speech"] if has_audio else ["preview"]
        )
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    other = f"/api/v1/projects/{ids['other']}/library"
    reused = post_ok(
        client,
        other + "/reuse",
        {
            "source_project_id": ids["project"],
            "source_version_id": version["id"],
        },
    )
    target = client.get(other + f"/versions/{reused['version_id']}").json()
    assert target["has_audio"] is has_audio
    assert target["original_hash"] == version["original_hash"]


def test_unknown_legacy_audio_metadata_is_not_treated_as_silent(library, monkeypatch):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    version = upload(client, base, content)
    with sessions() as db:
        db.get(MediaVersion, uuid.UUID(version["id"])).has_audio = None
        db.commit()
    configure_auto(monkeypatch)
    monkeypatch.setattr(settings, "library_mediacms_transcription_enabled", True)
    for provider, capabilities in (
        ("wise", ["audio", "visual"]),
        ("mediacms", ["preview", "speech"]),
    ):
        result = post_ok(
            client,
            f"{base}/versions/{version['id']}/process",
            {
                "provider": provider,
                "capabilities": capabilities,
            },
        )
        assert result["capabilities"] == capabilities
    assert client.get(f"{base}/versions/{version['id']}").json()["has_audio"] is None


@pytest.mark.parametrize("presence", [True, None])
def test_sound_only_auto_scan_skips_old_silent_video_without_starving_audio(
    library, monkeypatch, tmp_path, presence
):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    silent = upload(client, base, silent_copy(content, tmp_path))
    audible = upload(client, base, content)
    if presence is None:
        with sessions() as db:
            db.get(MediaVersion, uuid.UUID(audible["id"])).has_audio = None
            db.commit()
    configure_auto(monkeypatch)
    monkeypatch.setattr(settings, "library_processing_wise_auto_capabilities", ["audio"])
    monkeypatch.setattr(settings, "library_mediacms_token", None)
    with sessions() as db:
        assert processing.enqueue_automatic(db)
        assert processing.enqueue_automatic(db) is False
        rows = list(db.scalars(select(MediaProcessingRun)))
        assert len(rows) == 1
        assert str(rows[0].version_id) == audible["id"] != silent["id"]
        assert rows[0].capabilities == ["audio"]


def test_0031_keeps_old_presence_unknown_and_downgrade_retains_media(library_migration_schema):
    connection, config, schema = library_migration_schema
    ids = seed_legacy_library(connection, schema)
    command.upgrade(config, "0031_media_audio_presence")
    column = next(
        col
        for col in inspect(connection).get_columns("media_version", schema=schema)
        if col["name"] == "has_audio"
    )
    assert column["nullable"] and column["default"] is None
    assert (
        connection.scalar(
            text("SELECT has_audio FROM media_version WHERE id=:id"),
            {
                "id": ids["version"],
            },
        )
        is None
    )
    for value in (False, True):
        connection.execute(
            text("UPDATE media_version SET has_audio=:value WHERE id=:id"),
            {
                "value": value,
                "id": ids["version"],
            },
        )
        assert (
            connection.scalar(
                text("SELECT has_audio FROM media_version WHERE id=:id"),
                {
                    "id": ids["version"],
                },
            )
            is value
        )
    command.downgrade(config, "0030_annotation_substring_index")
    assert "has_audio" not in {
        col["name"] for col in inspect(connection).get_columns("media_version", schema=schema)
    }
    assert connection.scalar(text("SELECT count(*) FROM media_version")) == 1
    command.upgrade(config, "0031_media_audio_presence")
    assert (
        connection.scalar(
            text("SELECT has_audio FROM media_version WHERE id=:id"),
            {
                "id": ids["version"],
            },
        )
        is None
    )
