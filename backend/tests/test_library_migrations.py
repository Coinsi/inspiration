"""Fresh install and upgrade/downgrade checks confined to a random test schema."""

import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import CreateSchema, DropSchema

from alembic import command


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
