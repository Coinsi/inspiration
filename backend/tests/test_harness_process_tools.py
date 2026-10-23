"""Real local processing, strict recovery provenance and no implicit model costs."""

# ruff: noqa: F811
import copy
import io
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.config import settings
from app.models.agent import AgentRun
from app.models.engineering import EngineeringDelivery
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun
from app.models.identity import Membership
from app.models.storage import Blob
from app.modules.engineering import delivery
from app.modules.generation import jobs
from app.modules.harness import process_tools, technical_validation
from app.modules.tools import capabilities, registry
from app.modules.tools.invocation import invoke
from app.storage import cas
from tests.test_harness_editing_tools import context, ctx, png, source, work
from tests.test_harness_media_tools import setup_run
from tests.test_harness_technical_validation import video
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    process_tools.register_tools()


def arguments(work_id, ref, mode, **options):
    return {"work_id": work_id, "source": ref, "operation": {"mode": mode, **options}}


def jobs_count(db):
    return db.scalar(select(func.count()).select_from(GenerationJob))


def test_image_adjust_real_pixels_immutable_idempotent_and_independent_of_agent(studio):
    _, sessions, ids = studio
    work_id = work(studio)
    raw = png((200, 100, 50, 255), (80, 40))
    ref = source(sessions, ids, raw, library=True)
    task = context(work_id)
    args = arguments(
        work_id, ref, "image.adjust", crop=[0, 0, 0.5, 1], rotate=90, scale=0.5, brightness=0.5
    )
    with sessions() as db:
        out = invoke(db, ctx(db, ids), task, "media.process", args)
        assert out.status == "succeeded", out.model_dump()
        assert out.data["status"] == "succeeded" and out.data["materialized"]
        assert out.data["model_charge_points"] == 0 and not out.data["adopted"]
        generated = db.get(Generation, uuid.UUID(out.data["outputs"][0]["id"]))
        assert not generated.is_selected
        with Image.open(
            io.BytesIO(cas.read_bytes(db.get(Blob, generated.output_blob_hash)))
        ) as image:
            assert image.size == (20, 20)
            assert image.getpixel((10, 10))[:3] == (100, 50, 25)
        assert cas.read_bytes(db.get(Blob, ref["expected_blob_hash"])) == raw
        receipt = technical_validation.validate_output(
            db, ctx(db, ids), out.data["outputs"][0]["reference"]
        )
        assert receipt["status"] == "passed"
        repeated = invoke(db, ctx(db, ids), task, "media.process", args)
        assert repeated.data["job_id"] == out.data["job_id"] and repeated.data["reused"]
        changed = copy.deepcopy(args)
        changed["operation"]["brightness"] = 0.7
        denied = invoke(db, ctx(db, ids), task, "media.process", changed)
        assert denied.error.code == "VERSION_CONFLICT"
        assert jobs_count(db) == 1 and db.scalar(select(func.count()).select_from(AgentRun)) == 0
        assert db.get(EngineeringDelivery, uuid.UUID(out.data["job_id"])) is not None


@pytest.mark.parametrize(
    "mode,options,kind,count",
    [
        ("video.frames", {"times_ms": [0, 500]}, "image", 2),
        ("video.trim", {"start_ms": 100, "end_ms": 800}, "video", 1),
        ("video.extract_audio", {"start_ms": 100, "end_ms": 800}, "audio", 1),
    ],
)
def test_real_ffmpeg_video_derivatives_are_pinned_candidates(
    studio, tmp_path, mode, options, kind, count
):
    _, sessions, ids = studio
    work_id = work(studio)
    raw = video(tmp_path / "source.mp4")
    ref = source(sessions, ids, raw, "video", library=True, duration_ms=1000)
    with sessions() as db:
        out = invoke(
            db,
            ctx(db, ids),
            context(work_id),
            "media.process",
            arguments(work_id, ref, mode, **options),
        )
        assert out.status == "succeeded" and out.data["status"] == "succeeded", out.model_dump()
        assert len(out.data["outputs"]) == count
        for result in out.data["outputs"]:
            row = db.get(Generation, uuid.UUID(result["id"]))
            assert row.output_type == kind and not row.is_selected and row.cost_points == 0
            validation = technical_validation.validate_output(db, ctx(db, ids), result["reference"])
            assert validation["status"] == "passed", validation
            if kind != "image":
                assert abs(db.get(Blob, row.output_blob_hash).duration_ms - 700) <= 250
        assert cas.read_bytes(db.get(Blob, ref["expected_blob_hash"])) == raw


@pytest.mark.parametrize(
    "operation",
    [
        {"mode": "image.adjust", "crop": [0.8, 0, 0.5, 1]},
        {"mode": "video.frames", "times_ms": [0, 0]},
        {"mode": "video.frames", "times_ms": [1000]},
        {"mode": "video.trim", "start_ms": 800, "end_ms": 750},
        {"mode": "video.trim", "end_ms": 2000},
        {"mode": "video.generative_extend", "end_ms": 2000},
        {"mode": "audio.music_generation"},
    ],
)
def test_invalid_or_unsupported_operations_never_dispatch(studio, tmp_path, operation):
    _, sessions, ids = studio
    work_id = work(studio)
    ref = source(
        sessions, ids, video(tmp_path / "clip.mp4"), "video", library=True, duration_ms=1000
    )
    with sessions() as db:
        result = invoke(
            db,
            ctx(db, ids),
            context(work_id),
            "media.process",
            {"work_id": work_id, "source": ref, "operation": operation},
        )
        assert result.status == "failed", result.model_dump()
        assert jobs_count(db) == 0


def test_wrong_hash_wrong_project_role_and_missing_system_key_are_rejected(studio):
    _, sessions, ids = studio
    work_id = work(studio)
    ref = source(sessions, ids, png("red"), library=True)
    args = arguments(work_id, ref, "image.adjust")
    with sessions() as db:
        altered = copy.deepcopy(args)
        altered["source"]["expected_blob_hash"] = "0" * 64
        assert (
            invoke(db, ctx(db, ids), context(work_id), "media.process", altered).error.code
            == "VERSION_CONFLICT"
        )
        no_key = context(work_id)
        no_key.input_context = {}
        assert invoke(db, ctx(db, ids), no_key, "media.process", args).error.code == "FORBIDDEN"
        model_key = copy.deepcopy(args)
        model_key["request_key"] = str(uuid.uuid4())
        assert (
            invoke(db, ctx(db, ids), context(work_id), "media.process", model_key).status
            == "failed"
        )
        member = db.scalar(
            select(Membership).where(
                Membership.project_id == uuid.UUID(ids["project"]),
                Membership.user_id == ids["user"],
            )
        )
        member.role = "viewer"
        db.commit()
        assert (
            invoke(db, ctx(db, ids), context(work_id), "media.process", args).error.code
            == "FORBIDDEN"
        )
        assert jobs_count(db) == 0


def test_resource_reservation_rolls_back_job_and_paused_run_cannot_submit(studio):
    _, sessions, ids = studio
    work_id = work(studio)
    ref = source(sessions, ids, png("red"), library=True)
    run_id, tasks = setup_run(sessions, ids, budget=0)
    task = context(
        work_id,
        run_id=str(run_id),
        task_id=str(tasks[0]),
        lease_token=str(uuid.uuid5(tasks[0], "harness-media-test")),
    )
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        run.work_id = uuid.UUID(work_id)
        run.policy = {
            "effects": ["read", "candidate"],
            "budget_points": 0,
            "max_output_storage_bytes": 1,
        }
        db.commit()
        result = invoke(
            db, ctx(db, ids), task, "media.process", arguments(work_id, ref, "image.adjust")
        )
        assert result.error.code == "QUOTA_EXCEEDED", result.model_dump()
        assert jobs_count(db) == 0 and not run.context.get("resource_reservations")
        run.status = "paused"
        db.commit()
        result = invoke(
            db, ctx(db, ids), task, "media.process", arguments(work_id, ref, "image.adjust")
        )
        assert result.error.code == "VERSION_CONFLICT" and jobs_count(db) == 0


def test_outbox_restart_recovers_same_local_job_and_expired_worker_is_fenced(studio, monkeypatch):
    _, sessions, ids = studio
    work_id = work(studio)
    ref = source(sessions, ids, png("green"), library=True)
    monkeypatch.setattr(settings, "celery_eager", False)
    monkeypatch.setattr(delivery, "SessionLocal", sessions)
    task = context(work_id)
    args = arguments(work_id, ref, "image.adjust")
    with sessions() as db:
        result = invoke(db, ctx(db, ids), task, "media.process", args)
        job_id = uuid.UUID(result.data["job_id"])
        row = db.get(EngineeringDelivery, job_id)
        assert row and row.state == "pending"
        job = db.get(GenerationJob, job_id)
        token = delivery.claim(db, job)
        job.status = "running"
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert delivery.recover_expired() == 1
    with sessions() as db:
        assert not delivery.owned(db, job_id, token)
        result = invoke(db, ctx(db, ids), task, "media.process", args)
        assert result.data["job_id"] == str(job_id) and result.data["reused"]
        assert jobs_count(db) == 1
        jobs.execute(db, job_id)
        assert db.get(GenerationJob, job_id).status == "succeeded"
        count = db.scalar(select(func.count()).select_from(Generation))
        jobs.execute(db, job_id)
        assert db.scalar(select(func.count()).select_from(Generation)) == count == 1


def test_delivery_only_replays_explicit_local_whitelist():
    for provider, tool, operation, expected in [
        ("local", "media.process", "refine", True),
        ("local", "media.process", "video_trim", True),
        ("local", "media.process", "video_audio", True),
        ("local", "media.process", "video_frames", True),
        ("paid", "media.process", "video_trim", False),
        ("local", "media.generate", "video_trim", False),
        ("local", "media.process", "generate", False),
    ]:
        assert (
            delivery.eligible(
                SimpleNamespace(
                    provider=provider,
                    input_snapshot={"operation": operation, "harness_origin": {"tool": tool}},
                )
            )
            is expected
        )


def test_capability_inventory_does_not_claim_unimplemented_generation(studio, monkeypatch):
    _, sessions, ids = studio
    monkeypatch.setattr(settings, "transcriber_url", "")
    with sessions() as db:
        result = invoke(db, ctx(db, ids), context(work(studio)), "media.capabilities", {})
        assert result.status == "succeeded"
        limitations = {item["capability"]: item for item in result.data["limitations"]}
        assert not limitations["transcription"]["configured"]
        assert all(not value["harness_available"] for value in limitations.values())
        assert not limitations["audio.music_generation"]["domain_available"]
        assert limitations["image.semantic_inpaint"]["domain_available"]
        assert jobs_count(db) == 0
