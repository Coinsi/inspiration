"""Run migrations in an isolated schema, including downgrade preservation."""

# ruff: noqa: F811
from sqlalchemy import inspect, text

from alembic import command
from tests.test_library_migrations import library_migration_schema  # noqa: F401


def test_conversation_migration_roundtrip(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0031_media_audio_presence")
    tables_before = set(inspect(conn).get_table_names(schema=schema))
    command.upgrade(config, "0032_conversations")
    tables_after = set(inspect(conn).get_table_names(schema=schema))
    assert tables_after - tables_before == {"conversation", "conversation_message"}
    assert "input_context" in {
        c["name"] for c in inspect(conn).get_columns("agent_run", schema=schema)
    }
    indexes = inspect(conn).get_indexes("conversation_message", schema=schema)
    assert {i["name"] for i in indexes if i["unique"]} == {
        "uq_conversation_ordinal",
        "uq_conversation_request",
    }
    command.downgrade(config, "0031_media_audio_presence")
    assert set(inspect(conn).get_table_names(schema=schema)) == tables_before
    assert "input_context" not in {
        c["name"] for c in inspect(conn).get_columns("agent_run", schema=schema)
    }
    command.upgrade(config, "0032_conversations")
    assert (
        conn.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version'))
        == "0032_conversations"
    )


def test_material_migration_roundtrip(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0032_conversations")
    before = set(inspect(conn).get_table_names(schema=schema))
    command.upgrade(config, "0033_material_catalog")
    assert set(inspect(conn).get_table_names(schema=schema)) - before == {
        "generated_material_state"
    }
    assert inspect(conn).get_pk_constraint("generated_material_state", schema=schema)[
        "constrained_columns"
    ] == ["generation_id"]
    command.downgrade(config, "0032_conversations")
    assert set(inspect(conn).get_table_names(schema=schema)) == before
    command.upgrade(config, "0033_material_catalog")


def test_work_migration_roundtrip(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0033_material_catalog")
    before = set(inspect(conn).get_table_names(schema=schema))
    command.upgrade(config, "0034_creative_works")
    assert set(inspect(conn).get_table_names(schema=schema)) - before == {"creative_work"}
    for table in ("conversation", "workbench_creation"):
        assert "work_id" in {c["name"] for c in inspect(conn).get_columns(table, schema=schema)}
    command.downgrade(config, "0033_material_catalog")
    assert set(inspect(conn).get_table_names(schema=schema)) == before
    command.upgrade(config, "0034_creative_works")
