"""Exercise actual Alembic DDL only in disposable schemas."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from alembic import command
from tests.test_library_migrations import library_migration_schema  # noqa: F401


def test_archive_capacity_migration_roundtrip(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0047_archive_capacity")
    assert "reserved_bytes" in {
        c["name"] for c in inspect(conn).get_columns("engineering_archive_job", schema=schema)
    }
    command.downgrade(config, "0046_engineering_archive_uploads")
    command.upgrade(config, "0047_archive_capacity")


def test_archive_upload_migration_roundtrip(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0046_engineering_archive_uploads")
    columns = {
        c["name"]: c for c in inspect(conn).get_columns("engineering_archive_job", schema=schema)
    }
    assert {"operation", "filename", "expected_bytes", "uploaded_bytes"} <= columns.keys()
    assert not columns["expected_bytes"]["nullable"]
    command.downgrade(config, "0045_engineering_archive_jobs")
    assert "uploaded_bytes" not in {
        c["name"] for c in inspect(conn).get_columns("engineering_archive_job", schema=schema)
    }
    command.upgrade(config, "0046_engineering_archive_uploads")


def test_archive_job_migration_installs_reference_guards(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0045_engineering_archive_jobs")
    assert "engineering_archive_job" in inspect(conn).get_table_names(schema=schema)
    assert (
        conn.scalar(
            text("""SELECT count(*) FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND c.relname='engineering_archive_job'
        AND t.tgname='guard_blob_reference'"""),
            {"schema": schema},
        )
        == 1
    )
    command.downgrade(config, "0044_storage_deletion")
    assert "engineering_archive_job" not in inspect(conn).get_table_names(schema=schema)
    command.upgrade(config, "0045_engineering_archive_jobs")


def seed(conn):
    from app.models.identity import Project, User
    from app.models.narrative import Script

    with Session(bind=conn, join_transaction_mode="create_savepoint") as db:
        user = User(username="migration", email="migration@example.test", display_name="Migration")
        db.add(user)
        db.flush()
        project = Project(code="MIG", name="Migration", owner_id=user.id)
        db.add(project)
        db.flush()
        db.add(Script(project_id=project.id, code="SCR-1", title="Existing script"))
        db.commit()


def test_engineering_upgrade_preserves_old_work_and_roundtrips(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0037_material_display_names")
    seed(conn)
    wid = uuid.uuid4()
    conn.execute(
        text("""INSERT INTO creative_work(id,project_id,title,script_id,created_by)
        SELECT :wid,s.project_id,'legacy',s.id,p.owner_id FROM script s
        JOIN project p ON p.id=s.project_id LIMIT 1"""),
        {"wid": wid},
    )
    before = conn.execute(text("SELECT id,script_id FROM creative_work")).all()
    command.upgrade(config, "0038_engineering_foundation")
    assert conn.execute(text("SELECT id,script_id FROM creative_work")).all() == before
    assert conn.scalar(text("SELECT engineering_revision FROM creative_work")) == 0
    assert next(
        c
        for c in inspect(conn).get_columns("creative_work", schema=schema)
        if c["name"] == "script_id"
    )["nullable"]
    assert {"engineering_commit", "engineering_change", "engineering_resource"} <= set(
        inspect(conn).get_table_names(schema=schema)
    )
    command.downgrade(config, "0037_material_display_names")
    assert conn.execute(text("SELECT id,script_id FROM creative_work")).all() == before
    command.upgrade(config, "0038_engineering_foundation")


def test_downgrade_refuses_to_destroy_standalone_work(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0038_engineering_foundation")
    seed(conn)
    wid = uuid.uuid4()
    conn.execute(
        text("""INSERT INTO creative_work(id,project_id,title,created_by)
        SELECT :wid,id,'standalone',owner_id FROM project LIMIT 1"""),
        {"wid": wid},
    )
    with pytest.raises(RuntimeError, match="Standalone"):
        command.downgrade(config, "0037_material_display_names")
    assert conn.scalar(text("SELECT count(*) FROM creative_work WHERE id=:wid"), {"wid": wid}) == 1
    assert "engineering_commit" in inspect(conn).get_table_names(schema=schema)


def test_review_migration_roundtrip(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0038_engineering_foundation")
    seed(conn)
    wid, cid, key = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text("""INSERT INTO creative_work(id,project_id,title,created_by)
        SELECT :wid,id,'review',owner_id FROM project LIMIT 1"""),
        {"wid": wid},
    )
    conn.execute(
        text("""INSERT INTO engineering_change
        (id,work_id,created_by,request_key,fingerprint,base_revision,snapshot,status)
        SELECT :cid,:wid,owner_id,:key,:fp,0,'{}','proposed' FROM project LIMIT 1"""),
        {"cid": cid, "wid": wid, "key": key, "fp": "a" * 64},
    )
    command.upgrade(config, "0039_engineering_timeline_review")
    assert conn.scalar(text("SELECT timeline_update FROM engineering_change")) == {}
    command.downgrade(config, "0038_engineering_foundation")
    assert conn.scalar(text("SELECT count(*) FROM engineering_change")) == 1
    command.upgrade(config, "0039_engineering_timeline_review")
    conn.execute(
        text("UPDATE engineering_change SET timeline_update=CAST(:value AS jsonb)"),
        {"value": '{"test":true}'},
    )
    with pytest.raises(RuntimeError, match="Reviewed"):
        command.downgrade(config, "0038_engineering_foundation")


def test_document_migration_roundtrip(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0040_work_timeline_links")
    seed(conn)
    before = conn.execute(text("SELECT id,title FROM script")).all()
    command.upgrade(config, "0041_engineering_documents")
    assert {"engineering_document", "engineering_document_revision"} <= set(
        inspect(conn).get_table_names(schema=schema)
    )
    assert "document_revision" in {
        c["name"] for c in inspect(conn).get_columns("work_timeline", schema=schema)
    }
    assert conn.execute(text("SELECT id,title FROM script")).all() == before

    command.downgrade(config, "0040_work_timeline_links")
    command.upgrade(config, "0041_engineering_documents")
    assert conn.execute(text("SELECT id,title FROM script")).all() == before


def test_independent_clip_migration_refuses_lossy_downgrade(library_migration_schema):
    conn, config, schema = library_migration_schema
    command.upgrade(config, "0042_independent_timeline_clips")
    seed(conn)
    assert next(
        c
        for c in inspect(conn).get_columns("timeline_item", schema=schema)
        if c["name"] == "shot_id"
    )["nullable"]
    tid, item = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text(
            """INSERT INTO timeline(id,project_id,name,kind,status,revision,subtitles,visuals)
            SELECT :id,id,'independent','main','draft',0,'[]','[]' FROM project LIMIT 1"""
        ),
        {"id": tid},
    )
    conn.execute(
        text(
            """INSERT INTO timeline_item
            (id,timeline_id,generation_id,ordinal,in_point_ms,out_point_ms,duration_ms)
            VALUES(:id,:tid,:gen,0,0,0,1000)"""
        ),
        {"id": item, "tid": tid, "gen": uuid.uuid4()},
    )
    with pytest.raises(RuntimeError, match="Independent"):
        command.downgrade(config, "0041_engineering_documents")
    assert conn.scalar(text("SELECT count(*) FROM timeline_item WHERE id=:id"), {"id": item}) == 1
