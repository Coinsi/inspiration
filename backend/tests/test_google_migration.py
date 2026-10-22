"""Google transaction DDL roundtrip in an isolated PostgreSQL schema."""

# ruff: noqa: F811
from sqlalchemy import inspect, text

from alembic import command
from tests.test_library_migrations import library_migration_schema  # noqa: F401


def test_google_migration_preserves_existing_accounts(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0047_archive_capacity")
    conn.execute(text('''
        INSERT INTO "user" (id, username, email, display_name, is_active)
        VALUES ('53d18326-3644-48ee-8f9d-59ed76be2404',
                'existing_writer', 'existing@example.com', 'Existing writer', true)
    '''))
    existing = conn.execute(text('SELECT id, username, email FROM "user"')).all()

    command.upgrade(config, "0048_google_oauth")
    assert "oauth_transaction" in inspect(conn).get_table_names(schema=schema)
    assert "ix_oauth_transaction_expires_at" in {
        index["name"] for index in inspect(conn).get_indexes("oauth_transaction", schema=schema)
    }
    assert conn.execute(text('SELECT id, username, email FROM "user"')).all() == existing

    command.downgrade(config, "0047_archive_capacity")
    assert "oauth_transaction" not in inspect(conn).get_table_names(schema=schema)
    assert conn.execute(text('SELECT id, username, email FROM "user"')).all() == existing
    command.upgrade(config, "0048_google_oauth")
    assert "oauth_transaction" in inspect(conn).get_table_names(schema=schema)
