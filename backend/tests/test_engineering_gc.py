"""Deletion races and recovery use real PostgreSQL guards and disposable files only."""

# ruff: noqa: F811
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.generation import GenerationJob
from app.models.identity import Project
from app.models.storage import Blob, BlobDeletion
from app.storage import cas, gc_guards, lifecycle
from app.storage import garbage_collection as gc
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture
def guarded(studio, monkeypatch):
    _, sessions, ids = studio
    with sessions() as db:
        connection = db.connection()
        schema = connection.get_execution_options()["schema_translate_map"][None]
        gc_guards.install_v1(connection, schema)
        db.commit()
    monkeypatch.setattr(settings, "storage_gc_enabled", True)
    monkeypatch.setattr(lifecycle, "SessionLocal", sessions)
    return sessions, ids


def source(sessions):
    with sessions() as db:
        blob = cas.put_bytes(db, b"disposable GC source", "application/octet-stream")
        identity, uri = blob.hash, blob.storage_uri
        db.commit()
        operation = gc.mark(db, identity, grace_seconds=0)
        db.commit()
    return identity, uri, operation


def test_reader_prevents_claim_then_delete_is_idempotent(guarded):
    sessions, _ = guarded
    identity, uri, operation = source(sessions)
    with sessions() as db:
        blob = db.get(Blob, identity)
        with cas.local_file(blob) as path:
            with cas.open_range(blob) as stream:
                assert stream.read() == b"disposable GC source"
            with pytest.raises(gc.Deferred):
                gc.execute(sessions, operation)
            assert path.exists()
    assert gc.execute(sessions, operation) == "deleted"
    assert not Path(uri).exists()
    assert gc.execute(sessions, operation) == "deleted"
    with sessions() as db:
        with pytest.raises(FileNotFoundError):
            cas.read_bytes(db.get(Blob, identity))


def test_new_reference_after_mark_cancels_deletion(guarded):
    sessions, ids = guarded
    identity, uri, operation = source(sessions)
    with sessions() as db:
        db.get(Project, ids["project"]).cover_blob_hash = identity
        db.commit()
    assert gc.execute(sessions, operation) == "canceled"
    assert Path(uri).exists()


def test_tombstone_blocks_foreign_key_and_json_then_reupload_is_safe(guarded):
    sessions, ids = guarded
    identity, uri, operation = source(sessions)
    with sessions() as db:
        assert gc.claim(db, operation).status == "deleting"
        db.commit()
    with sessions() as db:
        db.get(Project, ids["project"]).cover_blob_hash = identity
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(
            GenerationJob(
                project_id=ids["project"],
                target_type="shot",
                target_id=ids["shot"],
                provider="local",
                status="pending",
                input_snapshot={"future": {identity: {}}},
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        new = cas.put_bytes(db, b"disposable GC source", "application/octet-stream")
        new_uri = new.storage_uri
        assert new_uri != uri
        db.get(Project, ids["project"]).cover_blob_hash = identity
        db.commit()
    assert gc.execute(sessions, operation) == "deleted"
    assert Path(new_uri).read_bytes() == b"disposable GC source"
    assert not Path(uri).exists()


def test_lost_delete_receipt_retries_old_identity(guarded, monkeypatch):
    sessions, _ = guarded
    identity, uri, operation = source(sessions)
    delete = gc.delete_object

    def lost_receipt(row):
        delete(row)
        raise ConnectionError("receipt lost")

    monkeypatch.setattr(gc, "delete_object", lost_receipt)
    with pytest.raises(ConnectionError):
        gc.execute(sessions, operation)
    assert not Path(uri).exists()
    with sessions() as db:
        assert db.get(BlobDeletion, operation).status == "deleting"
        new = cas.put_bytes(db, b"disposable GC source", "application/octet-stream")
        replacement = new.storage_uri
        db.commit()
    monkeypatch.setattr(gc, "delete_object", delete)
    assert gc.execute(sessions, operation) == "deleted"
    assert Path(replacement).exists()


def test_gc_requires_opt_in_and_schema_guard_coverage(studio, monkeypatch):
    _, sessions, _ = studio
    with sessions() as db:
        with pytest.raises(RuntimeError, match="disabled"):
            gc.mark(db, "a" * 64)
    monkeypatch.setattr(settings, "storage_gc_enabled", True)
    monkeypatch.setattr(lifecycle, "SessionLocal", sessions)
    _, uri, operation = source(sessions)
    with pytest.raises(RuntimeError, match="guards"):
        gc.execute(sessions, operation)
    assert Path(uri).exists()


def test_active_writer_defers_and_grace_period_preserves_file(guarded):
    sessions, _ = guarded
    identity, uri, operation = source(sessions)
    with sessions() as writer:
        cas.put_bytes(writer, b"disposable GC source", "application/octet-stream")
        with pytest.raises(gc.Deferred, match="writer"):
            gc.execute(sessions, operation)
        assert Path(uri).exists()
        writer.rollback()
    with sessions() as db:
        delayed = gc.mark(db, identity, grace_seconds=3600)
        db.commit()
    with pytest.raises(gc.Deferred, match="grace"):
        gc.execute(sessions, delayed)
    assert Path(uri).exists()


def test_captured_path_outside_cas_is_never_deleted(guarded, tmp_path):
    sessions, _ = guarded
    identity, _, operation = source(sessions)
    outside = tmp_path / "keep.txt"
    outside.write_text("keep")
    with sessions() as db:
        row = db.get(BlobDeletion, operation)
        row.storage_uri = str(outside)
        with pytest.raises(ValueError):
            gc.delete_object(row)
    assert outside.read_text() == "keep"
