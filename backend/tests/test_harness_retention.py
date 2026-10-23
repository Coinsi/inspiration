"""Production snapshots are durable CAS roots, including late and canceled work."""

# ruff: noqa: F811
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models.harness import ProductionAttempt, ProductionEvent, ProductionRun, ProductionTask
from app.storage import garbage_collection as gc
from app.storage import gc_guards, retention
from tests.test_engineering_gc import guarded, source  # noqa: F401
from tests.test_harness_runtime import seeded
from tests.test_media_workflow import studio  # noqa: F401


@pytest.mark.parametrize("owner", ["run", "task", "attempt", "event"])
def test_production_fixed_reference_after_mark_cancels_gc(guarded, studio, owner):
    sessions, _ = guarded
    _, _, run_id = seeded(studio)
    digest, uri, deletion_id = source(sessions)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        task = db.scalar(select(ProductionTask).where(ProductionTask.run_id == run_id))
        reference = {"expected_blob_hash": digest, "revision": 1}
        if owner == "run":
            run.context = {"references": [reference]}
        elif owner == "task":
            task.specification = {**task.specification, "references": [reference]}
        elif owner == "attempt":
            db.add(
                ProductionAttempt(
                    task_id=task.id,
                    number=1,
                    lease_token=uuid.uuid4(),
                    status="uncertain",
                    operation={"fixed_input": reference},
                    result={},
                    error={},
                )
            )
        else:
            run.event_sequence += 1
            db.add(
                ProductionEvent(
                    run_id=run.id,
                    sequence=run.event_sequence,
                    kind="guidance.received",
                    payload={"anchor": reference},
                )
            )
        run.status = "canceled"
        task.status = "superseded"
        db.commit()
    # A restarted session discovers the reference even after cancellation. GC
    # does not silently expire forensic provenance or a fixed review baseline.
    with sessions() as db:
        reasons = retention.inspect_blob(db, digest)["reasons"]
        assert any(reason["table"] == f"production_{owner}" for reason in reasons)
    assert gc.execute(sessions, deletion_id) == "canceled"
    assert Path(uri).exists()


def test_migration_covers_production_tables_and_blocks_deleted_object_references(
    guarded, studio, monkeypatch
):
    sessions, _ = guarded
    _, _, run_id = seeded(studio)
    digest, uri, deletion_id = source(sessions)
    path = Path(__file__).parents[1] / "alembic/versions/0053_harness_retention_guards.py"
    spec = importlib.util.spec_from_file_location("harness_retention_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0052_transcription_execution"
    with sessions() as db:
        connection = db.connection()
        schema = connection.get_execution_options()["schema_translate_map"][None]
        quote = connection.dialect.identifier_preparer.quote
        # Simulate the state after 0050: prior tables guarded, new tables not.
        for name in ("production_run", "production_task", "production_attempt", "production_event"):
            db.execute(text(f"DROP TRIGGER guard_blob_reference ON {quote(schema)}.{quote(name)}"))
        db.commit()
    with sessions() as db:
        with pytest.raises(RuntimeError, match="guards"):
            gc_guards.verify_and_lock(db)
        db.rollback()
        connection = db.connection()
        db.execute(text(f"SET LOCAL search_path TO {quote(schema)}"))
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        gc_guards.verify_and_lock(db)
        db.commit()
    assert gc.execute(sessions, deletion_id) == "deleted"
    assert not Path(uri).exists()
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        run.context = {"references": [{"expected_blob_hash": digest}]}
        with pytest.raises(IntegrityError, match="being reclaimed"):
            db.commit()
        db.rollback()
