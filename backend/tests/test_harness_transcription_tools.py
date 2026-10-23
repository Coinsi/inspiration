"""Real audio/checkpoints/database with an explicitly simulated recognition protocol.

The protocol adapter validates decoded PCM; these tests do not assess ASR quality.
"""

# ruff: noqa: F811
import base64
import io
import uuid
import wave

import pytest
from sqlalchemy import func, select, text

from app.core.config import settings
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.models.media_index import MediaAnnotation
from app.models.transcription import TranscriptionRun
from app.modules.harness import transcription_tools as tools
from app.modules.harness import transcription_wait as wait
from app.modules.tools import capabilities, registry
from app.modules.tools.invocation import invoke
from app.modules.transcription import service, worker
from tests.test_harness_editing_tools import ctx, sound, source
from tests.test_harness_media_tools import context, setup_run
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def register(monkeypatch, studio):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    monkeypatch.setattr(settings, "transcriber_url", "http://recognition-protocol-fixture.invalid")
    monkeypatch.setattr(settings, "harness_transcription_cost_mode", "self_hosted")
    monkeypatch.setattr(settings, "transcription_worker_enabled", True)
    tools.register_tools()


def setup(studio, seconds=1):
    _, sessions, ids = studio
    ref = source(sessions, ids, sound(seconds), "audio", library=True, duration_ms=seconds * 1000)
    run_id, tasks = setup_run(sessions, ids, budget=0)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        run.policy = {"effects": ["read", "candidate"], "budget_points": 0}
        db.commit()
    grant = context(run_id, tasks[0])
    return (
        sessions,
        ids,
        run_id,
        tasks[0],
        grant,
        {
            "version_id": ref["id"],
            "expected_blob_hash": ref["expected_blob_hash"],
            "language": "zh",
        },
    )


def simulated_protocol(monkeypatch, sessions, run_id, *, failed_chunk=None):
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    calls = []

    def inference(path, payload=None):
        # Another DB session can acquire the run lock: no recognition/health I/O
        # may retain the coordinator's lock. This also covers pause/cancel latency.
        with sessions() as db:
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            db.scalar(select(ProductionRun).where(ProductionRun.id == run_id).with_for_update())
        if path == "/health":
            calls.append("health")
            return {"model_key": "simulated-asr-protocol-v1"}
        assert path == "/transcribe"
        with wave.open(io.BytesIO(base64.b64decode(payload["audio"]))) as audio:
            assert audio.getnchannels() == 1 and audio.getframerate() == 16000
            length = round(audio.getnframes() * 1000 / audio.getframerate())
        calls.append(length)
        if (
            failed_chunk is not None
            and len([x for x in calls if isinstance(x, int)]) == failed_chunk
        ):
            raise ValueError("simulated transport outage")
        return {
            "model_key": "simulated-asr-protocol-v1",
            "segments": [{"start_ms": 100, "end_ms": min(length, 800), "text": "模拟识别协议结果"}],
        }

    monkeypatch.setattr(service, "inference", inference)
    return calls


def start(studio, monkeypatch, seconds=1):
    sessions, ids, run_id, task_id, grant, args = setup(studio, seconds)
    monkeypatch.setattr(
        service,
        "inference",
        lambda *a: pytest.fail("submit must not call health/model under locks"),
    )
    with sessions() as db:
        result = invoke(db, ctx(db, ids), grant, "media.transcribe", args)
        assert result.status == "succeeded", str(result.model_dump())
        ident = uuid.UUID(result.data["transcription_id"])
        row = db.get(TranscriptionRun, ident)
        assert row.status == "queued" and row.model_key == service.DEFERRED_MODEL_KEY
        assert row.published_revision is None and result.data["async_kind"] == "transcription"
    return sessions, ids, run_id, task_id, grant, args, ident


def waiting(sessions, task_id, ident, *, kind="agent"):
    with sessions() as db:
        task = db.get(ProductionTask, task_id)
        task.kind, task.status = kind, "waiting_transcription"
        task.state = {"waiting_transcription_id": str(ident)}
        task.lease_token, task.lease_expires_at = None, None
        db.commit()


def test_persist_intent_then_real_audio_decode_and_bounded_draft_read(studio, monkeypatch):
    sessions, ids, run_id, task_id, grant, args, ident = start(studio, monkeypatch)
    with sessions() as db:
        repeated = invoke(db, ctx(db, ids), grant, "media.transcribe", args)
        assert repeated.data["transcription_id"] == str(ident) and repeated.data["reused"]
        changed = invoke(db, ctx(db, ids), grant, "media.transcribe", {**args, "language": "en"})
        assert changed.error.code == "VERSION_CONFLICT"
    calls = simulated_protocol(monkeypatch, sessions, run_id)
    worker.process(ident)
    assert calls == ["health", 1000]
    with sessions() as db:
        result = invoke(
            db,
            ctx(db, ids),
            grant,
            "media.transcription_status",
            {"transcription_id": str(ident), "revision": 0},
        )
        assert result.status == "succeeded" and result.data["draft_ready"]
        assert result.data["cues"][0]["text"] == "模拟识别协议结果"
        assert result.data["coverage"]["word_alignment"] is False
        assert "recognition_accuracy" in result.data["unverified"]
        assert db.scalar(select(func.count()).select_from(MediaAnnotation)) == 0
        assert db.scalar(select(func.count()).select_from(TranscriptionRun)) == 1
        assert not result.adopted


@pytest.mark.parametrize("configuration", ["unknown_cost", "unconfigured", "worker_disabled"])
def test_unknown_cost_and_unconfigured_service_never_submit(studio, monkeypatch, configuration):
    sessions, ids, _, _, grant, args = setup(studio)
    if configuration == "unknown_cost":
        monkeypatch.setattr(settings, "harness_transcription_cost_mode", "unknown")
    elif configuration == "unconfigured":
        monkeypatch.setattr(settings, "transcriber_url", "")
    else:
        monkeypatch.setattr(settings, "transcription_worker_enabled", False)
    with sessions() as db:
        result = invoke(db, ctx(db, ids), grant, "media.transcribe", args)
        assert result.error.code == "CAPABILITY_UNSUPPORTED"
        assert not tools.availability()["available"]
        assert db.scalar(select(func.count()).select_from(TranscriptionRun)) == 0


def test_pending_then_paused_late_completion_keeps_pause_and_no_publish(studio, monkeypatch):
    sessions, ids, run_id, task_id, _, _, ident = start(studio, monkeypatch)
    waiting(sessions, task_id, ident)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        assert wait.poll_waiting(db, run)[0]["kind"] == "task.transcription_progress"
        assert wait.poll_waiting(db, run) == []
        run.status = "paused"
        db.commit()
    simulated_protocol(monkeypatch, sessions, run_id)
    worker.process(ident)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        result = wait.poll_waiting(db, run)
        assert result[0]["kind"] == "task.transcription_completed"
        assert run.status == "paused"
        task = db.get(ProductionTask, task_id)
        assert task.status == "queued" and len(task.state["steps"]) == 1
        assert not task.result["adopted"] and task.result["data"]["requires_review"]
        assert wait.poll_waiting(db, run) == []


def test_checkpoint_restart_only_decodes_unfinished_chunk(studio, monkeypatch):
    sessions, _, run_id, _, _, _, ident = start(studio, monkeypatch, seconds=31)
    calls = simulated_protocol(monkeypatch, sessions, run_id)

    def stopped():
        with sessions() as db:
            return db.get(TranscriptionRun, ident).completed == 1

    worker.process(ident, stopped)
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        assert row.completed == 1 and row.status == "queued"
        assert row.model_key == "simulated-asr-protocol-v1"
    worker.process(ident)
    assert calls == ["health", 30000, 1000]
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        assert row.status == "ready" and row.completed == 2 and len(row.cues) == 2
        assert row.cues[1]["start_ms"] == 30100


def test_cancel_mid_recognition_discards_late_chunk_and_preserves_other_tasks(studio, monkeypatch):
    sessions, _, run_id, task_id, _, _, ident = start(studio, monkeypatch)
    calls = simulated_protocol(monkeypatch, sessions, run_id)
    original = service.inference

    def inference(path, payload=None):
        result = original(path, payload)
        if path == "/transcribe":
            with sessions() as db:
                run = db.scalar(
                    select(ProductionRun).where(ProductionRun.id == run_id).with_for_update()
                )
                assert wait.cancel_owned(db, run, task_ids=[uuid.uuid4()]) == []
                assert wait.cancel_owned(db, run, task_ids=[task_id]) == [str(ident)]
                run.status = "canceled"
                db.commit()
        return result

    monkeypatch.setattr(service, "inference", inference)
    worker.process(ident)
    assert calls == ["health", 1000]
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        assert row.status == "canceled" and row.completed == 0 and row.cues == []
        assert wait.poll_waiting(db, db.get(ProductionRun, run_id)) == []


def test_worker_failure_is_visible_and_poll_never_retries_recognition(studio, monkeypatch):
    sessions, _, run_id, task_id, _, _, ident = start(studio, monkeypatch)
    waiting(sessions, task_id, ident, kind="tool")
    calls = simulated_protocol(monkeypatch, sessions, run_id, failed_chunk=1)
    worker.process(ident)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        result = wait.poll_waiting(db, run)
        assert result[0]["kind"] == "task.transcription_failed"
        task = db.get(ProductionTask, task_id)
        assert task.status == "failed" and task.error["retryable"] is False
        assert wait.poll_waiting(db, run) == []
        assert db.scalar(select(func.count()).select_from(TranscriptionRun)) == 1
    assert calls == ["health", 1000]


def test_lost_submission_receipt_recovers_exact_asr_intent_without_repeating(studio, monkeypatch):
    sessions, _, run_id, task_id, grant, args, ident = start(studio, monkeypatch)
    with sessions() as db:
        task = db.get(ProductionTask, task_id)
        attempt = ProductionAttempt(
            id=uuid.UUID(grant.input_context["execution"]["request_key"]),
            task_id=task_id,
            number=1,
            lease_token=task.lease_token,
            status="uncertain",
            operation={"phase": "tool", "tool": "media.transcribe", "arguments": args},
            result={},
            error={},
        )
        db.add(attempt)
        task.status = "uncertain"
        db.flush()
        run = db.get(ProductionRun, run_id)
        result = wait.recover_uncertain(db, run)
        assert result[0]["kind"] == "task.transcription_recovered"
        assert task.status == "waiting_transcription" and task.state[
            "waiting_transcription_id"
        ] == str(ident)
        assert db.scalar(select(func.count()).select_from(TranscriptionRun)) == 1
        task.status = "uncertain"
        run.context = {**run.context, "transcription_reservations": {}}
        assert wait.recover_uncertain(db, run) == [] and task.status == "uncertain"


def test_model_revision_drift_and_invalid_cue_ranges_are_not_accepted(studio, monkeypatch):
    sessions, ids, run_id, task_id, grant, _, ident = start(studio, monkeypatch)
    waiting(sessions, task_id, ident)
    simulated_protocol(monkeypatch, sessions, run_id)
    worker.process(ident)
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        row.revision = 1
        row.cues = [{**row.cues[0], "end_ms": 9000}]
        db.commit()
        assert (
            invoke(
                db,
                ctx(db, ids),
                grant,
                "media.transcription_status",
                {"transcription_id": str(ident), "revision": 0},
            ).error.code
            == "VERSION_CONFLICT"
        )
        result = wait.poll_waiting(db, db.get(ProductionRun, run_id))
        assert result[0]["kind"] == "task.transcription_unresolved"
        assert db.get(ProductionTask, task_id).status == "uncertain"


@pytest.mark.parametrize("drift", ["endpoint", "cost", "source", "membership"])
def test_execution_snapshot_rejects_restart_drift_before_any_network(studio, monkeypatch, drift):
    from app.models.identity import Membership
    from app.models.library import MediaVersion

    sessions, ids, run_id, _, _, _, ident = start(studio, monkeypatch)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        frozen = row.execution_snapshot
        assert frozen["cost_mode"] == "self_hosted" and frozen["duration_ms"] == 1000
        assert "http" not in str(frozen) and len(frozen["endpoint_digest"]) == 64
        if drift == "source":
            db.get(MediaVersion, row.version_id).duration_ms = 2000
        elif drift == "membership":
            member = db.scalar(
                select(Membership).where(
                    Membership.project_id == uuid.UUID(ids["project"]),
                    Membership.user_id == ids["user"],
                )
            )
            member.role = "viewer"
        db.commit()
    if drift == "endpoint":
        monkeypatch.setattr(
            settings, "transcriber_url", "http://changed-and-not-authorized.invalid"
        )
    elif drift == "cost":
        monkeypatch.setattr(settings, "harness_transcription_cost_mode", "unknown")
    worker.process(ident)
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        assert row.status == "failed" and row.completed == 0 and row.cues == []


def test_source_hash_is_checked_before_sending_audio_to_recognizer(studio, monkeypatch):
    from app.models.storage import Blob
    from app.storage import cas

    sessions, _, run_id, _, _, _, ident = start(studio, monkeypatch)
    calls = simulated_protocol(monkeypatch, sessions, run_id)
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        with cas.local_file(db.get(Blob, row.execution_snapshot["source_blob_hash"])) as path:
            path.write_bytes(b"tampered source")
    worker.process(ident)
    assert calls == ["health"]  # No audio was sent to the simulated ASR endpoint.
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        assert row.status == "failed" and row.completed == 0


def test_existing_api_keeps_synchronous_health_and_empty_execution_snapshot(studio, monkeypatch):
    from app.modules.harness.context import revalidate_context
    from app.modules.transcription.schemas import Create

    sessions, ids, run_id, _, _, args = setup(studio)
    calls = simulated_protocol(monkeypatch, sessions, run_id)
    with sessions() as db:
        result = service.start(
            db,
            revalidate_context(db, ctx(db, ids)),
            Create(version_id=args["version_id"], language="zh"),
        )
        row = db.get(TranscriptionRun, result["id"])
        assert calls == ["health"]
        assert row.model_key == "simulated-asr-protocol-v1" and row.execution_snapshot == {}


def test_transcription_ready_cannot_satisfy_video_artifact_contract(studio, monkeypatch):
    sessions, _, run_id, task_id, _, _, ident = start(studio, monkeypatch)
    waiting(sessions, task_id, ident, kind="tool")
    with sessions() as db:
        db.get(ProductionTask, task_id).specification = {"expected_output": "artifact"}
        db.commit()
    simulated_protocol(monkeypatch, sessions, run_id)
    worker.process(ident)
    with sessions() as db:
        wait.poll_waiting(db, db.get(ProductionRun, run_id))
        task = db.get(ProductionTask, task_id)
        assert task.status == "failed" and task.error["code"] == "TRANSCRIPTION_OUTPUT_MISMATCH"
        assert db.get(TranscriptionRun, ident).status == "ready"  # Preserve the usable draft.
