"""Actual production tool execution, bounded budgets and durable output receipts."""

# ruff: noqa: F811
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.adapters.contracts import Capabilities
from app.adapters.generation.mock import MockProvider
from app.models.agent import AgentRun
from app.models.creation import Creation
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.models.identity import Project, User
from app.models.storage import Blob
from app.modules.generation import jobs
from app.modules.harness import media_tools
from app.modules.tools import capabilities, registry
from app.modules.tools.contracts import ToolContext
from app.modules.tools.invocation import invoke
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    media_tools.register_tools()


def ctx(db, ids):
    return SimpleNamespace(
        project=db.get(Project, uuid.UUID(ids["project"])), user=db.get(User, ids["user"])
    )


def setup_run(sessions, ids, budget=100, task_count=1):
    with sessions() as db:
        run = ProductionRun(
            project_id=uuid.UUID(ids["project"]),
            created_by=ids["user"],
            request_key=uuid.uuid4(),
            goal="独立制作工具验收",
            status="running",
            engine="mock",
            revision=1,
            event_sequence=0,
            policy={"budget_points": budget, "effects": ["read", "generate"]},
            context={},
            plan={},
            result={},
            error={},
        )
        db.add(run)
        db.flush()
        tasks = [
            ProductionTask(
                run_id=run.id,
                key=f"media{i}",
                goal="制作候选",
                kind="tool",
                role="media",
                status="running",
                specification={},
                state={},
                result={},
                error={},
            )
            for i in range(task_count)
        ]
        db.add_all(tasks)
        db.flush()
        for task in tasks:
            task.lease_token = uuid.uuid5(task.id, "harness-media-test")
            task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=10)
        run_id, task_ids = run.id, [t.id for t in tasks]
        db.commit()
    return run_id, task_ids


def context(run_id, task_id, **updates):
    return ToolContext(
        input_context={
            "execution": {
                "run_id": str(run_id),
                "task_id": str(task_id),
                "request_key": str(uuid.uuid4()),
                "generation_authorized": True,
                "lease_token": str(uuid.uuid5(task_id, "harness-media-test")),
                **updates,
            }
        }
    )


def args(**updates):
    return {"provider": "mock", "prompt": "清晨山间的云海", "request_type": "image", **updates}


def test_estimate_then_authorized_generation_is_materialized_and_idempotent(studio):
    _, sessions, ids = studio
    run_id, tasks = setup_run(sessions, ids)
    task = context(run_id, tasks[0])
    with sessions() as db:
        user = ctx(db, ids)
        estimate = invoke(db, user, task, "media.estimate", args())
        assert estimate.status == "succeeded" and estimate.data["estimated_points"] == 10
        assert estimate.data["submitted"] is False
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        denied = invoke(db, user, task, "media.generate", args())
        assert denied.status == "failed" and denied.error.code == "FORBIDDEN"
        result = invoke(db, user, task, "media.generate", args(), allow_charges=True)
        assert result.status == "succeeded", result.model_dump()
        assert result.data["materialized"] and result.data["is_mock"]
        assert result.data["status"] == "succeeded" and not result.data["adopted"]
        assert result.data["outputs"][0]["is_selected"] is False
        repeated = invoke(db, user, task, "media.generate", args(), allow_charges=True)
        assert repeated.data["job_id"] == result.data["job_id"]
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
        run = db.get(ProductionRun, run_id)
        assert len(run.context["budget_reservations"]) == 1
        altered = invoke(
            db, user, task, "media.generate", args(prompt="另一个要求"), allow_charges=True
        )
        assert altered.status == "failed" and altered.error.code == "VERSION_CONFLICT"
        decoded = invoke(
            db,
            user,
            task,
            "media.inspect",
            {
                "reference": result.data["outputs"][0]["reference"],
            },
        )
        assert decoded.status == "succeeded", decoded.model_dump()
        assert decoded.data["content_parts"][0]["image_url"]["url"].startswith("data:image/")
        assert "model_interpretation" in decoded.data["unobserved"]


def test_model_cannot_supply_key_or_bypass_task_authorization(studio):
    _, sessions, ids = studio
    run_id, tasks = setup_run(sessions, ids)
    with sessions() as db:
        user = ctx(db, ids)
        spoofed = invoke(
            db,
            user,
            context(run_id, tasks[0]),
            "media.generate",
            args(request_key=str(uuid.uuid4())),
            allow_charges=True,
        )
        assert spoofed.status == "failed"
        absent = invoke(db, user, ToolContext(), "media.generate", args(), allow_charges=True)
        assert absent.status == "failed" and absent.error.code == "FORBIDDEN"
        run = db.get(ProductionRun, run_id)
        run.status = "paused"
        db.commit()
        paused = invoke(
            db, user, context(run_id, tasks[0]), "media.generate", args(), allow_charges=True
        )
        assert paused.status == "failed" and paused.error.category == "conflict"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_estimate_over_budget_creates_no_partial_creation(studio):
    _, sessions, ids = studio
    run_id, tasks = setup_run(sessions, ids, budget=5)
    with sessions() as db:
        result = invoke(
            db,
            ctx(db, ids),
            context(run_id, tasks[0]),
            "media.generate",
            args(),
            allow_charges=True,
        )
        assert result.status == "failed" and result.error.code == "QUOTA_EXCEEDED"
        assert db.scalar(select(func.count()).select_from(Creation)) == 0
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        assert not db.get(ProductionRun, run_id).context.get("budget_reservations")


def test_parallel_generation_reservations_cannot_overrun_budget(studio, monkeypatch):
    _, sessions, ids = studio
    run_id, tasks = setup_run(sessions, ids, budget=60, task_count=2)

    def queued(db, job):
        db.commit()
        return job

    monkeypatch.setattr(jobs, "dispatch", queued)

    def submit(task_id):
        with sessions() as db:
            result = invoke(
                db,
                ctx(db, ids),
                context(run_id, task_id),
                "media.generate",
                args(request_type="video"),
                allow_charges=True,
            )
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, tasks))
    assert sorted(r.status for r in results) == ["failed", "succeeded"]
    assert next(r for r in results if r.status == "failed").error.code == "QUOTA_EXCEEDED"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert len(db.get(ProductionRun, run_id).context["budget_reservations"]) == 1


@pytest.mark.parametrize("source_kind", ["image", "video"])
def test_model_capability_checked_and_reference_reaches_generation(
    studio, monkeypatch, source_kind
):
    _, sessions, ids = studio
    run_id, tasks = setup_run(sessions, ids, budget=150)
    with sessions() as db:
        user = ctx(db, ids)
        first = invoke(
            db,
            user,
            context(run_id, tasks[0]),
            "media.generate",
            args(request_type=source_kind),
            allow_charges=True,
        )
        assert first.status == "succeeded" and first.data["materialized"], first.model_dump()
        source_id = first.data["outputs"][0]["id"]
        request = args(request_type="video", references=[{"generation_id": source_id}])
        unsupported = invoke(
            db, user, context(run_id, tasks[0]), "media.generate", request, allow_charges=True
        )
        assert unsupported.status == "failed" and unsupported.error.code == "CAPABILITY_UNSUPPORTED"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        # A deliberately capable offline fixture, not evidence of real model quality.
        monkeypatch.setattr(
            MockProvider,
            "capabilities",
            lambda self: Capabilities(
                modalities={"image", "video"},
                features={"reference", "video_reference"},
                max_reference_images=4,
                max_reference_videos=2,
                max_video_seconds=10,
            ),
        )
        second = invoke(
            db, user, context(run_id, tasks[0]), "media.generate", request, allow_charges=True
        )
        assert second.status == "succeeded" and second.data["materialized"], second.model_dump()
        job = db.get(GenerationJob, uuid.UUID(second.data["job_id"]))
        assert job.input_snapshot["references"][0]["kind"] == source_kind
        assert job.input_snapshot["reference_sources"][0]["generation_id"] == source_id


def test_observe_distinguishes_missing_storage_and_rejects_cross_project(studio, monkeypatch):
    _, sessions, ids = studio
    run_id, tasks = setup_run(sessions, ids)
    with sessions() as db:
        user = ctx(db, ids)
        result = invoke(
            db, user, context(run_id, tasks[0]), "media.generate", args(), allow_charges=True
        )
        assert result.data["materialized"]
        job_id = result.data["job_id"]
        generation = db.get(Generation, uuid.UUID(result.data["outputs"][0]["id"]))
        blob = db.get(Blob, generation.output_blob_hash)
        monkeypatch.setattr(blob, "storage_uri", "C:/definitely-missing-harness-output.png")
        observed = invoke(db, user, ToolContext(), "media.job_observe", {"job_id": job_id})
        assert observed.status == "succeeded" and observed.data["status"] == "succeeded"
        assert not observed.data["materialized"]
        foreign = copy.copy(user)
        foreign.project = db.get(Project, ids["other"])
        outside = invoke(db, foreign, ToolContext(), "media.job_observe", {"job_id": job_id})
        assert outside.status == "failed" and outside.error.category == "authorization"


def test_preview_status_follows_actual_candidate_output(studio):
    from tests.test_engineering_review import draft, preview, ready

    client, sessions, ids, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    arguments = {"work_id": path.split("/")[-2], "change_id": change["id"]}
    with sessions() as db:
        result = invoke(db, ctx(db, ids), ToolContext(), "engineering.preview_status", arguments)
        assert result.data["status"] == "not_started" and result.data["job_id"] is None
    rendered = preview(client, path, change)
    with sessions() as db:
        result = invoke(db, ctx(db, ids), ToolContext(), "engineering.preview_status", arguments)
        assert result.status == "succeeded" and result.data["materialized"], result.model_dump()
        assert result.data["job_id"] == rendered["preview_job_id"]
        assert result.data["adopted"] is False


def test_stale_execution_lease_cannot_submit(studio):
    _, sessions, ids = studio
    run_id, tasks = setup_run(sessions, ids)
    with sessions() as db:
        old = context(run_id, tasks[0])
        task = db.get(ProductionTask, tasks[0])
        task.lease_token = uuid.uuid4()
        db.commit()
        denied = invoke(db, ctx(db, ids), old, "media.generate", args(), allow_charges=True)
        assert denied.status == "failed" and denied.error.code == "VERSION_CONFLICT"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
