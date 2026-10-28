"""Verify the deploy path from the previous Alembic revision, in a random schema."""

# ruff: noqa: F811
from sqlalchemy import inspect, text

from alembic import command
from app.models.attachment import Attachment
from tests.test_library_migrations import library_migration_schema  # noqa: F401


def test_attachment_migration_from_0054(library_migration_schema):
    connection, config, schema = library_migration_schema
    command.upgrade(config, "0054_sdk_video_handles")
    before = set(inspect(connection).get_table_names(schema=schema))
    command.upgrade(config, "0055_conversation_attachments")
    assert set(inspect(connection).get_table_names(schema=schema)) - before == {
        "conversation_attachment"
    }
    columns = inspect(connection).get_columns("conversation_attachment", schema=schema)
    assert {column["name"] for column in columns} == set(Attachment.__table__.columns.keys())
    indexes = inspect(connection).get_indexes("conversation_attachment", schema=schema)
    assert {index["name"] for index in indexes if index["unique"]} == {"uq_attachment_request"}
    command.downgrade(config, "0054_sdk_video_handles")
    assert set(inspect(connection).get_table_names(schema=schema)) == before
    command.upgrade(config, "0055_conversation_attachments")
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "0055_conversation_attachments"
    )
