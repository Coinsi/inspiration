"""Real receipt recovery: poll the original handle, save once, never submit again."""

# ruff: noqa: F811
import copy
import hashlib
import uuid
from decimal import Decimal
from urllib.error import URLError

import pytest
from sqlalchemy import func, select

from app.adapters.contracts import Capabilities, GenerationResult, Output, generation_registry
from app.core.crypto import encrypt
from app.models.creation import Creation
from app.models.generation import (
    CreativeModel,
    Generation,
    GenerationJob,
    ModelChannel,
    ProviderConfig,
    Quota,
)
from app.models.harness import ProductionRun, ProductionTask
from app.models.identity import Membership
from app.models.personal_model import PersonalModelService
from app.modules.generation import jobs, model_catalog, personal
from app.modules.harness import media_tools, recovery_tools, runtime, schemas, service
from app.modules.harness.context import revalidate_context
from app.modules.tools import capabilities, registry
from app.modules.tools.invocation import invoke
from tests.test_harness_media_tools import context, ctx, setup_run
from tests.test_harness_runtime import registered_fixture_kernel  # noqa: F401
from tests.test_media_workflow import changing_video, studio  # noqa: F401


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    media_tools.register_tools()
    recovery_tools.register_tools()


def failed_job(studio, mode="routed", phase="generated"):
    _, sessions, ids = studio
    rid, tasks = setup_run(sessions, ids)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        run.policy = schemas.Policy(budget_points=100, effects=["read", "generate"]).model_dump()
        db.add(Quota(project_id=run.project_id, scope="project", limit_cost=1000, used_cost=0))
        config = {
            "name": "Fixture video",
            "modality": "video",
            "protocol": "ark_video",
            "model": "fixture-video",
            "revision": 1,
        }
        if mode == "personal":
            row = PersonalModelService(
                user_id=ids["user"],
                name="Personal fixture",
                preset_id="custom-fixture",
                endpoint="https://fixture.invalid",
                credentials_encrypted=encrypt("fixture-key"),
                models={"fixture": config},
                revision=1,
            )
            db.add(row)
            db.flush()
            provider = personal.PREFIX + row.id.hex + "_fixture"
            snapshot = {
                "personal_service": {"id": str(row.id), "key": "fixture", "revision": 1},
                "model_profile": personal.profile(config),
            }
            route_name, connection_id = provider, row.id
        else:
            channel = ModelChannel(
                project_id=None if mode == "platform" else run.project_id,
                name="Fixture channel",
                endpoint="https://fixture.invalid",
                credentials_encrypted=encrypt("fixture-key"),
                revision=1,
            )
            db.add(channel)
            db.flush()
            config = {**config, "channel_id": str(channel.id)}
            profile = ProviderConfig(
                project_id=None if mode == "platform" else run.project_id,
                provider_name="platform_fixture" if mode == "platform" else "fixture-video-profile",
                config=config,
            )
            model = CreativeModel(
                project_id=None if mode == "platform" else run.project_id,
                name="Fixture routed",
                modality="video",
                revision=1,
                definition={},
            )
            db.add_all([profile, model])
            db.flush()
            route_name = profile.provider_name
            route = {
                "profile_id": str(profile.id),
                "provider_name": route_name,
                "channel_id": str(channel.id),
                "channel_revision": 1,
                "endpoint_hash": hashlib.sha256(channel.endpoint.encode()).hexdigest(),
                "channel_name": channel.name,
                "model_name": "Fixture",
                "model_profile": copy.deepcopy(config),
                "cost_points": 7,
            }
            snapshot = {
                "routing": {
                    "model_id": str(model.id),
                    "name": model.name,
                    "revision": 1,
                    "candidates": [route],
                }
            }
            provider, connection_id = model_catalog.PREFIX + model.id.hex, channel.id
        original_key = uuid.uuid4()
        creation = Creation(
            project_id=run.project_id,
            created_by=ids["user"],
            request_key=original_key,
            title="Original",
            request={},
        )
        db.add(creation)
        db.flush()
        snapshot.update(
            request_type="video",
            prompt="A calm lake",
            count=1,
            references=[],
            harness_origin={
                "run_id": str(rid),
                "task_id": str(tasks[0]),
                "request_key": str(original_key),
                "plan_revision": 1,
            },
        )
        job = GenerationJob(
            project_id=run.project_id,
            created_by=ids["user"],
            target_type="creation",
            target_id=creation.id,
            provider=provider,
            request_type="video",
            status="failed",
            external_job_id="original-remote-123",
            estimated_cost=7,
            error="fixture download interrupted",
            input_snapshot=snapshot,
            routing_state={
                "phase": phase,
                "provider_name": route_name,
                "external_job_id": "original-remote-123",
                "receipt_poll_tracking": True,
            },
        )
        db.add(job)
        db.flush()
        run.context = {
            "budget_reservations": {
                str(original_key): {"job_id": str(job.id), "estimated_points": 7}
            }
        }
        db.commit()
        return sessions, ids, rid, tasks[0], job.id, connection_id


def adapter(monkeypatch, data, *, url=False):
    counts = {"submit": 0, "poll": []}

    class Video:
        def capabilities(self):
            return Capabilities(modalities={"video"})

        def submit(self, req):
            counts["submit"] += 1
            pytest.fail("Receipt recovery called adapter.submit")

        def poll(self, handle):
            counts["poll"].append(handle.external_job_id)
            return GenerationResult(
                status="succeeded",
                outputs=[Output(type="video", url="https://fixture.invalid/output.mp4")]
                if counts.get("url", url)
                else [Output(type="video", data=data)],
            )

    original = generation_registry.create
    monkeypatch.setattr(
        generation_registry,
        "create",
        lambda name, **kw: Video() if name == "ark_video" else original(name, **kw),
    )
    return counts


def receive(db, ids, rid, tid, jid, execution=None):
    return invoke(
        db,
        ctx(db, ids),
        execution or context(rid, tid),
        "media.resume_receipt",
        {"job_id": str(jid)},
    )


@pytest.mark.parametrize("mode", ["routed", "personal", "platform"])
@pytest.mark.parametrize("phase", ["accepted", "generated"])
def test_original_handle_is_polled_and_saved_once_without_submission(
    studio, changing_video, monkeypatch, mode, phase
):
    sessions, ids, rid, tid, jid, _ = failed_job(studio, mode, phase)
    counts = adapter(monkeypatch, changing_video)
    execution = context(rid, tid)
    with sessions() as db:
        before = db.scalar(
            select(Quota).where(Quota.project_id == uuid.UUID(ids["project"]))
        ).used_cost
        result = receive(db, ids, rid, tid, jid, execution)
        assert result.status == "succeeded", result.model_dump()
        assert result.data["status"] == "succeeded", db.get(GenerationJob, jid).error
        assert result.data["receipt_only"] and result.data["materialized"]
        repeated = receive(db, ids, rid, tid, jid, execution)
        assert repeated.data["reused_request"]
        assert counts == {"submit": 0, "poll": ["original-remote-123"]}
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert db.scalar(select(func.count()).select_from(Creation)) == 1
        assert db.scalar(select(func.count()).select_from(Generation)) == 1
        quota = db.scalar(select(Quota).where(Quota.project_id == uuid.UUID(ids["project"])))
        db.refresh(quota)
        assert quota.used_cost == before + Decimal(7)
        assert len(db.get(ProductionRun, rid).context["budget_reservations"]) == 1
        denied = receive(db, ids, rid, tid, jid)
        assert denied.status == "failed" and counts["submit"] == 0


def test_download_failure_can_repeat_reception_with_new_key_and_no_second_charge(
    studio, changing_video, monkeypatch
):
    sessions, ids, rid, tid, jid, _ = failed_job(studio)
    counts = adapter(monkeypatch, changing_video, url=True)
    monkeypatch.setattr(
        jobs.urllib.request,
        "urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(URLError("fixture download unavailable")),
    )
    with sessions() as db:
        first = receive(db, ids, rid, tid, jid)
        assert first.status == "succeeded" and first.data["status"] == "failed"
        job = db.get(GenerationJob, jid)
        assert job.actual_cost is None and recovery_tools.resume_problem(job) is None
        counts["url"] = False
        second = receive(db, ids, rid, tid, jid)
        assert second.data["status"] == "succeeded", db.get(GenerationJob, jid).error
        assert counts["submit"] == 0 and counts["poll"] == ["original-remote-123"] * 2
        assert db.scalar(select(func.count()).select_from(Generation)) == 1


@pytest.mark.parametrize(
    "phase", ["dispatching", "submission_unknown", "upstream_failed", "rejected"]
)
def test_uncertain_submission_or_upstream_failure_is_never_reissued(studio, monkeypatch, phase):
    sessions, ids, rid, tid, jid, _ = failed_job(studio, phase=phase)
    counts = adapter(monkeypatch, b"")
    with sessions() as db:
        result = receive(db, ids, rid, tid, jid)
        assert result.status == "failed"
        assert counts == {"submit": 0, "poll": []}
        assert db.get(GenerationJob, jid).status == "failed"


@pytest.mark.parametrize("mutation", ["config", "permission", "handle", "resume_removed"])
def test_worker_rechecks_configuration_permissions_and_handle_after_dispatch(
    studio, changing_video, monkeypatch, mutation
):
    sessions, ids, rid, tid, jid, cid = failed_job(studio)
    counts = adapter(monkeypatch, changing_video)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: db.commit())
    with sessions() as db:
        queued = receive(db, ids, rid, tid, jid)
        assert queued.data["status"] == "pending"
        job = db.get(GenerationJob, jid)
        if mutation == "config":
            channel = db.get(ModelChannel, cid)
            channel.credentials_encrypted = encrypt("rotated-fixture-key")
            channel.revision += 1
        elif mutation == "permission":
            db.delete(
                db.scalar(
                    select(Membership).where(
                        Membership.user_id == ids["user"],
                        Membership.project_id == uuid.UUID(ids["project"]),
                    )
                )
            )
        elif mutation == "handle":
            job.external_job_id = "different-job"
        else:
            job.routing_state = {k: v for k, v in job.routing_state.items() if k != "resume"}
        db.commit()
        jobs.execute(db, jid)
        db.refresh(job)
        assert job.status == "failed" and job.actual_cost is None
        assert counts == {"submit": 0, "poll": []}


def test_cross_run_scope_recovery_limit_and_existing_output_are_rejected(studio, monkeypatch):
    sessions, ids, rid, tid, jid, _ = failed_job(studio)
    other_run, other_tasks = setup_run(sessions, ids)
    counts = adapter(monkeypatch, b"")
    with sessions() as db:
        assert receive(db, ids, other_run, other_tasks[0], jid).status == "failed"
        db.rollback()
        job = db.get(GenerationJob, jid)
        job.routing_state = {
            **job.routing_state,
            recovery_tools.MARKER: {"request_keys": ["one", "two"]},
        }
        db.commit()
        assert receive(db, ids, rid, tid, jid).status == "failed"
        job.routing_state = {
            k: v for k, v in job.routing_state.items() if k != recovery_tools.MARKER
        }
        db.add(
            Generation(
                project_id=job.project_id,
                job_id=job.id,
                target_type=job.target_type,
                target_id=job.target_id,
                output_type="video",
                provider=job.provider,
            )
        )
        db.commit()
        assert receive(db, ids, rid, tid, jid).status == "failed"
        assert counts == {"submit": 0, "poll": []}


@pytest.mark.parametrize("mode", ["routed", "personal"])
def test_changed_original_configuration_is_rejected_before_dispatch(studio, monkeypatch, mode):
    sessions, ids, rid, tid, jid, cid = failed_job(studio, mode)
    counts = adapter(monkeypatch, b"")
    with sessions() as db:
        row = db.get(PersonalModelService if mode == "personal" else ModelChannel, cid)
        row.revision += 1
        db.commit()
        result = receive(db, ids, rid, tid, jid)
        assert result.status == "failed"
        assert db.get(GenerationJob, jid).status == "failed"
        assert counts == {"submit": 0, "poll": []}


def test_personal_poll_failure_is_explicit_and_old_ambiguous_records_remain_blocked(
    studio, monkeypatch
):
    sessions, ids, rid, tid, jid, _ = failed_job(studio, "personal", "accepted")

    class FailedVideo:
        def capabilities(self):
            return Capabilities(modalities={"video"})

        def submit(self, _):
            pytest.fail("Failed original job must never be resubmitted")

        def poll(self, handle):
            assert handle.external_job_id == "original-remote-123"
            return GenerationResult(status="failed", error="fixture upstream failure")

    monkeypatch.setattr(generation_registry, "create", lambda *a, **kw: FailedVideo())
    with sessions() as db:
        result = receive(db, ids, rid, tid, jid)
        assert result.data["status"] == "failed"
        job = db.get(GenerationJob, jid)
        assert job.routing_state["phase"] == "upstream_failed"
        assert recovery_tools.resume_problem(job)
        job.routing_state = {
            "phase": "accepted",
            "provider_name": job.provider,
            "external_job_id": job.external_job_id,
        }
        assert recovery_tools.resume_problem(job)


def test_user_control_executes_fixed_receipt_recovery_through_runtime(
    studio, changing_video, monkeypatch
):
    sessions, ids, rid, tid, jid, _ = failed_job(studio)
    counts = adapter(monkeypatch, changing_video)
    with sessions() as db:
        run, task = db.get(ProductionRun, rid), db.get(ProductionTask, tid)
        run.engine, run.status = "fixture", "failed"
        task.status, task.lease_token, task.lease_expires_at = "failed", None, None
        task.state = {"waiting_job_id": str(jid)}
        task.specification = {"expected_output": "artifact", "allowed_tools": ["media.generate"]}
        db.commit()
        control = schemas.Control(
            request_key=uuid.uuid4(),
            expected_revision=run.revision,
            action="resume_receipt",
            task_ids=[tid],
        )
        service.control(db, revalidate_context(db, ctx(db, ids)), rid, control)
        db.commit()
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions=sessions)
    with sessions() as db:
        task = db.get(ProductionTask, tid)
        assert task.status in {"queued", "waiting_job", "validating_job"}, task.error
        assert db.get(GenerationJob, jid).status == "succeeded", db.get(GenerationJob, jid).error
        assert counts == {"submit": 0, "poll": ["original-remote-123"]}
        assert task.specification["allowed_tools"] == ["media.generate"]
