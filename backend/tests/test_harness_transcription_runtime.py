"""Real WAV pipeline across coordinator checkpoints; ASR decisions are simulated."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import select

from app.models.harness import ProductionRun, ProductionTask
from app.models.transcription import TranscriptionRun
from app.modules.harness import runtime, service
from app.modules.transcription import service as transcription
from app.modules.transcription import worker
from tests.test_harness_editing_tools import sound, source
from tests.test_harness_runtime import (  # noqa: F401
    command,
    plan,
    registered_fixture_kernel,
    seeded,
)
from tests.test_harness_transcription_tools import register, simulated_protocol  # noqa: F401
from tests.test_media_workflow import studio  # noqa: F401


def submit(studio, monkeypatch):
    _, sessions, ids = studio
    ref = source(sessions, ids, sound(1), "audio", library=True, duration_ms=1000)
    sessions, ids, rid = seeded(
        studio,
        plan(
            (
                "recognize",
                {
                    "kind": "tool",
                    "tool": "media.transcribe",
                    "arguments": {
                        "version_id": ref["id"],
                        "expected_blob_hash": ref["expected_blob_hash"],
                    },
                    "expected_output": "observation",
                },
            ),
            (
                "inspect_project",
                {
                    "kind": "tool",
                    "tool": "project.overview",
                    "dependencies": ["recognize"],
                    "expected_output": "observation",
                },
            ),
        ),
        effects=["read", "candidate"],
    )
    monkeypatch.setattr(
        transcription, "inference", lambda *args: pytest.fail("submission does no network I/O")
    )
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions=sessions)
    with sessions() as db:
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert task.status == "waiting_transcription", task.error
        ident = uuid.UUID(task.state["waiting_transcription_id"])
        assert db.get(TranscriptionRun, ident).status == "queued"
        assert service.claim(db, rid) is None
        db.commit()
    return sessions, ids, rid, uuid.UUID(ticket["task_id"]), ident


def test_runtime_transcription_completion_wakes_real_dependent_tool(studio, monkeypatch):
    sessions, ids, rid, tid, ident = submit(studio, monkeypatch)
    calls = simulated_protocol(monkeypatch, sessions, rid)
    worker.process(ident)
    assert calls == ["health", 1000]
    with sessions() as db:
        ticket = service.claim(db, rid)
        assert (
            ticket and db.get(ProductionTask, uuid.UUID(ticket["task_id"])).key == "inspect_project"
        )
        done = db.get(ProductionTask, tid)
        assert done.status == "succeeded" and done.result["data"]["requires_review"]
        assert len(done.state["steps"]) >= 2  # submission plus real transcription receipt
        db.commit()
    runtime.execute(ticket, sessions=sessions)
    with sessions() as db:
        assert db.get(ProductionRun, rid).status == "succeeded"
        assert db.get(TranscriptionRun, ident).published_revision is None


def test_control_pause_accepts_late_transcription_receipt_then_resume_dispatches(
    studio, monkeypatch
):
    sessions, ids, rid, tid, ident = submit(studio, monkeypatch)
    with sessions() as db:
        command(db, ids, rid, "pause")
        db.commit()
    simulated_protocol(monkeypatch, sessions, rid)
    worker.process(ident)
    with sessions() as db:
        assert service.claim(db, rid) is None
        db.commit()
        assert db.get(ProductionRun, rid).status == "paused"
        assert db.get(ProductionTask, tid).status == "succeeded"
        command(db, ids, rid, "resume")
        db.commit()
        ticket = service.claim(db, rid)
        assert (
            ticket and db.get(ProductionTask, uuid.UUID(ticket["task_id"])).key == "inspect_project"
        )
        db.commit()
    runtime.execute(ticket, sessions=sessions)
    with sessions() as db:
        assert db.get(ProductionRun, rid).status == "succeeded"


def test_control_cancel_mid_recognition_fences_late_cues_and_successor(studio, monkeypatch):
    sessions, ids, rid, tid, ident = submit(studio, monkeypatch)
    calls = simulated_protocol(monkeypatch, sessions, rid)
    protocol = transcription.inference

    def cancel_after_decoding(path, payload=None):
        reply = protocol(path, payload)
        if path == "/transcribe":
            with sessions() as db:
                command(db, ids, rid, "cancel")
                db.commit()
        return reply

    monkeypatch.setattr(transcription, "inference", cancel_after_decoding)
    worker.process(ident)
    assert calls == ["health", 1000]
    with sessions() as db:
        assert service.claim(db, rid) is None
        assert db.get(ProductionRun, rid).status == "canceled"
        row = db.get(TranscriptionRun, ident)
        assert row.status == "canceled" and row.cues == [] and row.completed == 0
        assert db.get(ProductionTask, tid).status == "canceled"
        tasks = list(db.scalars(select(ProductionTask).where(ProductionTask.run_id == rid)))
        assert all(task.status == "canceled" for task in tasks)
