"""Fresh install and upgrade/downgrade checks confined to a random test schema."""

import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from alembic import command


@pytest.fixture
def library_migration_schema(monkeypatch):
    """An actual 0027 installation; never run migrations on the caller's schema."""
    from app.core.config import settings

    # Alembic runs inside pytest here; retain pytest's handlers and logger state.
    monkeypatch.setattr("logging.config.fileConfig", lambda *args, **kwargs: None)

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required")
    monkeypatch.setattr(settings, "enable_pgvector", False)
    engine = create_engine(url)
    schema = "test_migration_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(CreateSchema(schema))
    try:
        with engine.begin() as conn:
            conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            assert conn.scalar(text("SELECT current_schema()")) == schema
            config = Config("alembic.ini")
            config.attributes["connection"] = conn
            config.attributes["version_table_schema"] = schema
            command.upgrade(config, "0027_media_processing")
            yield conn, config, schema
    finally:
        with engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def seed_legacy_library(conn, schema):
    """Use real FKs and 0027 column metadata, including a pre-upgrade ranged use."""
    from app.models.identity import Project, User

    with Session(bind=conn, join_transaction_mode="create_savepoint") as db:
        user = User(username="migration", email="migration@example.test", display_name="Migration")
        db.add(user)
        db.flush()
        project = Project(code="MIGRATION", name="Migration", owner_id=user.id)
        db.add(project)
        db.flush()
        # This database is deliberately older than the current ORM models.
        # Reflect the historical tables instead of inserting newly added columns.
        legacy = MetaData()
        scripts = Table("script", legacy, schema=schema, autoload_with=conn)
        scenes = Table("scene", legacy, schema=schema, autoload_with=conn)
        shots = Table("shot", legacy, schema=schema, autoload_with=conn)
        script_id, scene_id, shot_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        db.execute(scripts.insert().values(
            id=script_id, project_id=project.id, code="SCR-1", title="Script",
            status="draft", content_blocks=[],
        ))
        db.execute(scenes.insert().values(
            id=scene_id, project_id=project.id, script_id=script_id,
            code="SC-1", ordinal=1, title="Scene", body="", status="draft",
        ))
        db.execute(shots.insert().values(
            id=shot_id, project_id=project.id, scene_id=scene_id,
            code="SH-1", ordinal=1, title="Shot", description="",
            production_status="draft", status="draft",
        ))
        media_table = Table("library_media", legacy, schema=schema, autoload_with=conn)
        media_id = uuid.uuid4()
        db.execute(media_table.insert().values(
            id=media_id, project_id=project.id, name="Legacy video",
        ))
        ids = {"shot": shot_id, "media": media_id, "version": uuid.uuid4()}
        db.commit()
    metadata = MetaData()
    versions = Table("media_version", metadata, schema=schema, autoload_with=conn)
    usages = Table("media_usage", metadata, schema=schema, autoload_with=conn)
    assert "media_type" not in versions.c
    conn.execute(
        versions.insert().values(
            id=ids["version"],
            media_id=ids["media"],
            ordinal=1,
            filename="legacy.mp4",
            fingerprint="a" * 64,
            size_bytes=16,
            uploaded_bytes=16,
            chunk_hashes=[],
            status="ready",
            duration_ms=2000,
            width=320,
            height=180,
        )
    )
    ids["usage"] = uuid.uuid4()
    conn.execute(
        usages.insert().values(
            id=ids["usage"],
            version_id=ids["version"],
            shot_id=ids["shot"],
            start_ms=100,
            end_ms=900,
            purpose="visual_reference",
            note="legacy range",
        )
    )
    return ids


def current_revision(conn, schema):
    return conn.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version'))


def test_0028_backfills_legacy_video_and_video_only_downgrade_preserves_rows(
    library_migration_schema,
):
    conn, config, schema = library_migration_schema
    ids = seed_legacy_library(conn, schema)
    command.upgrade(config, "0028_library_media_types")
    versions = Table("media_version", MetaData(), schema=schema, autoload_with=conn)
    old = conn.execute(select(versions).where(versions.c.id == ids["version"])).mappings().one()
    assert old["media_type"] == "video" and old["filename"] == "legacy.mp4"
    assert old["duration_ms"] == 2000 and old["fingerprint"] == "a" * 64
    added = {key: value for key, value in old.items() if key not in ("media_type", "id", "ordinal")}
    new_id = uuid.uuid4()
    conn.execute(versions.insert().values(**added, id=new_id, ordinal=2))
    assert conn.scalar(select(versions.c.media_type).where(versions.c.id == new_id)) == "video"
    command.downgrade(config, "0027_media_processing")
    assert current_revision(conn, schema) == "0027_media_processing"
    assert "media_type" not in {
        column["name"] for column in inspect(conn).get_columns("media_version", schema=schema)
    }
    assert conn.scalar(text("SELECT count(*) FROM media_version")) == 2
    usage = conn.execute(
        text("SELECT start_ms,end_ms,note FROM media_usage WHERE id=:id"), {"id": ids["usage"]}
    ).one()
    assert tuple(usage) == (100, 900, "legacy range")
    assert all(
        not column["nullable"]
        for column in inspect(conn).get_columns("media_usage", schema=schema)
        if column["name"] in ("start_ms", "end_ms")
    )
    command.upgrade(config, "0028_library_media_types")
    assert conn.scalar(text("SELECT count(*) FROM media_version WHERE media_type='video'")) == 2


def test_0028_whole_usage_null_pair_and_partial_unique_index(library_migration_schema):
    conn, config, schema = library_migration_schema
    ids = seed_legacy_library(conn, schema)
    command.upgrade(config, "0028_library_media_types")
    usages = Table("media_usage", MetaData(), schema=schema, autoload_with=conn)
    base = {
        "version_id": ids["version"],
        "shot_id": ids["shot"],
        "purpose": "visual_reference",
        "note": "whole",
    }
    whole = uuid.uuid4()
    conn.execute(usages.insert().values(**base, id=whole, start_ms=None, end_ms=None))
    for start, end in [(None, 100), (0, None), (-1, 100), (100, 100), (101, 100)]:
        with pytest.raises(IntegrityError) as error, conn.begin_nested():
            conn.execute(
                usages.insert().values(**base, id=uuid.uuid4(), start_ms=start, end_ms=end)
            )
        assert error.value.orig.diag.constraint_name == "ck_media_usage_range"
    with pytest.raises(IntegrityError) as error, conn.begin_nested():
        conn.execute(usages.insert().values(**base, id=uuid.uuid4(), start_ms=None, end_ms=None))
    assert error.value.orig.diag.constraint_name == "uq_media_usage_whole"
    # Whole references are unique per purpose; ranged references remain distinct.
    conn.execute(
        usages.insert().values(
            **{**base, "purpose": "editing_source"}, id=uuid.uuid4(), start_ms=None, end_ms=None
        )
    )
    conn.execute(usages.insert().values(**base, id=uuid.uuid4(), start_ms=200, end_ms=1000))
    assert conn.scalar(select(usages.c.id).where(usages.c.id == whole)) == whole
    assert conn.scalar(text("SELECT count(*) FROM media_usage")) == 4
    # No savepoint here: it could hide destructive DDL before the guard runs.
    with pytest.raises(RuntimeError, match="whole-image references"):
        command.downgrade(config, "0027_media_processing")
    assert current_revision(conn, schema) == "0028_library_media_types"
    assert conn.scalar(text("SELECT count(*) FROM media_usage")) == 4
    assert (
        conn.scalar(
            text("SELECT count(*) FROM media_usage WHERE start_ms IS NULL AND end_ms IS NULL")
        )
        == 2
    )


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_0028_refuses_lossy_downgrade_without_changing_media(library_migration_schema, kind):
    conn, config, schema = library_migration_schema
    ids = seed_legacy_library(conn, schema)
    command.upgrade(config, "0028_library_media_types")
    versions = Table("media_version", MetaData(), schema=schema, autoload_with=conn)
    conn.execute(
        versions.update()
        .where(versions.c.id == ids["version"])
        .values(media_type=kind, duration_ms=None if kind == "image" else 2000)
    )
    before = conn.execute(select(versions)).mappings().all()
    with pytest.raises(RuntimeError, match="Cannot downgrade while image/audio"):
        command.downgrade(config, "0027_media_processing")
    assert current_revision(conn, schema) == "0028_library_media_types"
    assert conn.execute(select(versions)).mappings().all() == before
    assert conn.execute(
        text("SELECT start_ms,end_ms,note FROM media_usage WHERE id=:id"),
        {"id": ids["usage"]},
    ).one() == (100, 900, "legacy range")
    assert "uq_media_usage_whole" in {
        index["name"] for index in inspect(conn).get_indexes("media_usage", schema=schema)
    }
    assert {
        constraint["name"]
        for constraint in inspect(conn).get_check_constraints("media_version", schema=schema)
    } >= {"ck_media_version_type"}


def test_fresh_install_and_library_upgrade(monkeypatch):
    from app.core.config import settings

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required")
    monkeypatch.setattr(settings, "enable_pgvector", False)
    engine = create_engine(url)
    schema = "test_migration_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(CreateSchema(schema))
    try:
        with engine.begin() as conn:
            conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            config = Config("alembic.ini")
            config.attributes["connection"] = conn
            config.attributes["version_table_schema"] = schema
            command.upgrade(config, "0005_setting")
            assert not inspect(conn).has_table("library_media", schema=schema)
            command.upgrade(config, "head")
            assert "visuals" in {
                c["name"] for c in inspect(conn).get_columns("timeline", schema=schema)
            }
            assert inspect(conn).has_table("media_usage", schema=schema)
            assert inspect(conn).has_table("media_reuse", schema=schema)
            assert inspect(conn).has_table("media_folder", schema=schema)
            assert "cover_blob_hash" in {
                c["name"] for c in inspect(conn).get_columns("project", schema=schema)
            }
            columns = {c["name"] for c in inspect(conn).get_columns("media_version", schema=schema)}
            assert {"chunk_hashes", "original_hash", "proxy_hash"} <= columns
            command.downgrade(config, "0005_setting")
            assert "visuals" not in {
                c["name"] for c in inspect(conn).get_columns("timeline", schema=schema)
            }
            assert not inspect(conn).has_table("media_usage", schema=schema)
            assert inspect(conn).has_table("shot", schema=schema)
            assert "cover_blob_hash" not in {
                c["name"] for c in inspect(conn).get_columns("project", schema=schema)
            }
            command.upgrade(config, "head")
    finally:
        with engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_0029_backfills_dispatched_only_and_roundtrips_without_losing_jobs(
    library_migration_schema,
):
    conn, config, schema = library_migration_schema
    ids = seed_legacy_library(conn, schema)
    command.upgrade(config, "0028_library_media_types")
    metadata = MetaData()
    blobs = Table("blob", metadata, schema=schema, autoload_with=conn)
    runs = Table("media_processing_run", metadata, schema=schema, autoload_with=conn)
    conn.execute(
        blobs.insert().values(hash="b" * 64, storage_uri="fixture", mime="video/mp4", size_bytes=16)
    )
    for status, attempts in [("queued", 0), ("ready", 3)]:
        conn.execute(
            runs.insert().values(
                id=uuid.uuid4(),
                version_id=ids["version"],
                provider="wise",
                operation="process",
                capabilities=["visual"],
                source_hash="b" * 64,
                status=status,
                progress={},
                outputs={},
                attempts=attempts,
                cancel_requested=False,
            )
        )
    before = conn.execute(select(runs).order_by(runs.c.id)).mappings().all()
    command.upgrade(config, "0029_media_queue_fairness")
    migrated = Table("media_processing_run", MetaData(), schema=schema, autoload_with=conn)
    rows = conn.execute(select(migrated).order_by(migrated.c.id)).mappings().all()
    for old, new in zip(before, rows, strict=True):
        assert new["last_dispatched_at"] == (old["updated_at"] if old["attempts"] else None)
        assert {key: value for key, value in new.items() if key != "last_dispatched_at"} == old
    command.downgrade(config, "0028_library_media_types")
    assert conn.execute(select(runs).order_by(runs.c.id)).mappings().all() == before
