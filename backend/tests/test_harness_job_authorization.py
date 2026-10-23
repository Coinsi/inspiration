"""Real persistence and media with explicitly simulated provider responses."""

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.adapters.generation.mock import MockProvider
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.models.identity import Membership, Project, User
from app.models.library import LibraryMedia, MediaVersion
from app.modules.generation import jobs, media_engine
from app.modules.harness import media_tools, process_tools
from app.modules.tools import capabilities, registry
from app.modules.tools.invocation import invoke
from app.storage import cas
from tests.test_harness_editing_tools import context as local_context
from tests.test_harness_editing_tools import ctx, png, source, work
from tests.test_harness_media_tools import args, context, setup_run
from tests.test_harness_process_tools import arguments
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    media_tools.register_tools()
    process_tools.register_tools()


def persist_only(monkeypatch):
    def dispatch(db, job):
        db.commit()
        return job

    monkeypatch.setattr(jobs, "dispatch", dispatch)


def staged_generate(studio, monkeypatch):
    persist_only(monkeypatch)
    _, sessions, ids = studio
    rid, tasks = setup_run(sessions, ids)
    with sessions() as db:
        out = invoke(
            db, ctx(db, ids), context(rid, tasks[0]), "media.generate", args(), allow_charges=True
        )
        assert out.status == "succeeded", out.model_dump()
        return sessions, ids, rid, tasks[0], uuid.UUID(out.data["job_id"])


def revoke(sessions, ids, kind="role", *, rid=None, tid=None):
    with sessions() as db:
        if kind == "role":
            member = db.scalar(
                select(Membership).where(
                    Membership.project_id == uuid.UUID(ids["project"]),
                    Membership.user_id == ids["user"],
                )
            )
            member.role = "viewer"
        elif kind == "member_removed":
            member = db.scalar(
                select(Membership).where(
                    Membership.project_id == uuid.UUID(ids["project"]),
                    Membership.user_id == ids["user"],
                )
            )
            db.delete(member)
        elif kind == "project_deleted":
            db.get(Project, uuid.UUID(ids["project"])).deleted_at = datetime.now(UTC)
        elif kind == "user_inactive":
            db.get(User, ids["user"]).is_active = False
        elif kind == "task_superseded":
            db.get(ProductionTask, tid).status = "superseded"
        elif kind == "effect_revoked":
            run = db.get(ProductionRun, rid)
            run.policy = {**run.policy, "effects": ["read"]}
        db.commit()


def failed_without_output(sessions, jid):
    with sessions() as db:
        job = db.get(GenerationJob, jid)
        assert job.status == "failed" and job.error, job.error
        assert (
            db.scalar(select(func.count()).select_from(Generation).where(Generation.job_id == jid))
            == 0
        )
        return job.external_job_id


@pytest.mark.parametrize(
    "kind",
    [
        "role",
        "member_removed",
        "project_deleted",
        "user_inactive",
        "task_superseded",
        "effect_revoked",
    ],
)
def test_live_revocation_before_execution_prevents_any_provider_call(studio, monkeypatch, kind):
    sessions, ids, rid, tid, jid = staged_generate(studio, monkeypatch)
    monkeypatch.setattr(
        MockProvider, "submit", lambda *a: pytest.fail("revoked task called a provider")
    )
    revoke(sessions, ids, kind, rid=rid, tid=tid)
    with sessions() as db:
        jobs.execute(db, jid)
    assert failed_without_output(sessions, jid) is None
    with sessions() as db:
        assert db.get(ProductionRun, rid).context[
            "budget_reservations"
        ]  # unknown charge is never released


def test_revocation_between_materialization_and_submit_is_rechecked(studio, monkeypatch):
    from app.modules.generation import service

    sessions, ids, rid, tid, jid = staged_generate(studio, monkeypatch)
    original = service._materialize_references

    def materialize(*a, **kw):
        result = original(*a, **kw)
        revoke(sessions, ids)
        return result

    monkeypatch.setattr(service, "_materialize_references", materialize)
    monkeypatch.setattr(
        MockProvider, "submit", lambda *a: pytest.fail("revoked task called a provider")
    )
    with sessions() as db:
        jobs.execute(db, jid)
    assert failed_without_output(sessions, jid) is None


def test_revocation_after_remote_acceptance_keeps_handle_and_attempts_cancel(studio, monkeypatch):
    sessions, ids, rid, tid, jid = staged_generate(studio, monkeypatch)
    submitted, canceled = MockProvider.submit, []

    def submit(self, req):
        result = submitted(self, req)
        revoke(sessions, ids)
        return result

    monkeypatch.setattr(MockProvider, "submit", submit)
    monkeypatch.setattr(
        MockProvider, "poll", lambda *a: pytest.fail("revoked task polled provider")
    )
    monkeypatch.setattr(
        MockProvider, "cancel", lambda self, handle: canceled.append(handle.external_job_id)
    )
    with sessions() as db:
        jobs.execute(db, jid)
    assert failed_without_output(sessions, jid) == "mock-job"
    assert canceled == ["mock-job"]


def test_revocation_after_provider_finishes_prevents_cas_and_publication(studio, monkeypatch):
    sessions, ids, rid, tid, jid = staged_generate(studio, monkeypatch)
    poll = MockProvider.poll

    def result_and_revoke(self, handle):
        result = poll(self, handle)
        revoke(sessions, ids)
        return result

    monkeypatch.setattr(MockProvider, "poll", result_and_revoke)
    monkeypatch.setattr(
        cas, "put_bytes", lambda *a, **kw: pytest.fail("revoked output reached CAS")
    )
    with sessions() as db:
        jobs.execute(db, jid)
    assert failed_without_output(sessions, jid) == "mock-job"


def staged_process(studio, monkeypatch):
    _, sessions, ids = studio
    wid = work(studio)
    ref = source(sessions, ids, png((100, 80, 60, 255)), library=True)
    persist_only(monkeypatch)
    with sessions() as db:
        out = invoke(
            db,
            ctx(db, ids),
            local_context(wid),
            "media.process",
            arguments(wid, ref, "image.adjust", brightness=0.5),
        )
        assert out.status == "succeeded", out.model_dump()
        return sessions, ids, ref, uuid.UUID(out.data["job_id"])


@pytest.mark.parametrize("change", ["trash", "hash"])
def test_fixed_source_change_after_enqueue_stops_local_processing(studio, monkeypatch, change):
    sessions, ids, ref, jid = staged_process(studio, monkeypatch)
    alternate = source(sessions, ids, png((10, 30, 60, 255)), library=True)
    with sessions() as db:
        row = db.get(MediaVersion, uuid.UUID(ref["id"]))
        if change == "trash":
            db.get(LibraryMedia, row.media_id).deleted_at = datetime.now(UTC)
        else:
            row.original_hash = alternate["expected_blob_hash"]
        db.commit()
    monkeypatch.setattr(
        media_engine, "transform_image", lambda *a: pytest.fail("invalid source was processed")
    )
    with sessions() as db:
        jobs.execute(db, jid)
    failed_without_output(sessions, jid)


def test_permission_revoked_during_cas_blocks_final_database_publication(studio, monkeypatch):
    sessions, ids, ref, jid = staged_process(studio, monkeypatch)
    put = cas.put_bytes

    def write_then_revoke(*a, **kw):
        result = put(*a, **kw)
        revoke(sessions, ids)
        return result

    monkeypatch.setattr(cas, "put_bytes", write_then_revoke)
    with sessions() as db:
        jobs.execute(db, jid)
    failed_without_output(sessions, jid)


def test_pause_keeps_inflight_receipt_without_implicitly_resuming(studio, monkeypatch):
    sessions, ids, rid, tid, jid = staged_generate(studio, monkeypatch)
    with sessions() as db:
        db.get(ProductionRun, rid).status = "paused"
        db.commit()
        jobs.execute(db, jid)
        assert db.get(GenerationJob, jid).status == "succeeded"
        assert db.get(ProductionRun, rid).status == "paused"
        assert not db.scalar(select(Generation).where(Generation.job_id == jid)).is_selected


@pytest.mark.parametrize("change", ["cancel", "steer", "failed", "effect_revoked"])
def test_accepted_paid_output_survives_plan_change_without_reviving_old_task(
    studio, monkeypatch, change
):
    from app.modules.harness.schemas import Policy
    from tests.test_harness_runtime import command

    sessions, ids, rid, tid, jid = staged_generate(studio, monkeypatch)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        run.policy = Policy(**run.policy).model_dump()
        db.commit()
    submitted, calls, statuses = MockProvider.submit, [], []

    def submit_then_change(self, req):
        calls.append("submit")
        handle = submitted(self, req)
        with sessions() as db:
            if change == "cancel":
                command(db, ids, rid, "cancel")
            elif change == "steer":
                command(db, ids, rid, "steer", task_ids=[tid], message="改用另一种画面，保留旧候选")
            elif change == "failed":
                db.get(ProductionRun, rid).status = "failed"
                db.get(ProductionTask, tid).status = "failed"
            else:
                run = db.get(ProductionRun, rid)
                run.policy = {**run.policy, "effects": ["read"]}
            db.commit()
            statuses.extend([db.get(ProductionRun, rid).status, db.get(ProductionTask, tid).status])
        return handle

    monkeypatch.setattr(MockProvider, "submit", submit_then_change)
    with sessions() as db:
        jobs.execute(db, jid)
        job = db.get(GenerationJob, jid)
        assert job.status == "succeeded", job.error
        assert job.external_job_id == "mock-job" and float(job.actual_cost) == 10
        output = db.scalar(select(Generation).where(Generation.job_id == jid))
        assert output and not output.is_selected
        assert [db.get(ProductionRun, rid).status, db.get(ProductionTask, tid).status] == statuses
        assert db.get(ProductionTask, tid).result == {}
    assert calls == ["submit"]


@pytest.mark.parametrize("explicit", [False, True])
def test_linked_work_is_frozen_in_actual_generation_snapshot(studio, monkeypatch, explicit):
    persist_only(monkeypatch)
    _, sessions, ids = studio
    wid = uuid.UUID(work(studio))
    rid, tasks = setup_run(sessions, ids)
    with sessions() as db:
        db.get(ProductionRun, rid).work_id = wid
        db.commit()
        grant = context(rid, tasks[0])
        grant.work_id = wid
        out = invoke(
            db,
            ctx(db, ids),
            grant,
            "media.generate",
            args(work_id=str(wid)) if explicit else args(),
            allow_charges=True,
        )
        assert out.status == "succeeded", out.model_dump()
        jid = uuid.UUID(out.data["job_id"])
        job = db.get(GenerationJob, jid)
        assert job.input_snapshot["work_id"] == str(wid)
        jobs.execute(db, jid)
        assert db.get(GenerationJob, jid).status == "succeeded", job.error


def test_generation_reference_hash_is_rechecked_before_provider_submission(studio, monkeypatch):
    from app.adapters.contracts import Capabilities

    persist_only(monkeypatch)
    _, sessions, ids = studio
    ref = source(sessions, ids, png((70, 90, 100, 255)))
    other = source(sessions, ids, png((90, 110, 120, 255)))
    rid, tasks = setup_run(sessions, ids)
    monkeypatch.setattr(
        MockProvider,
        "capabilities",
        lambda self: Capabilities(
            modalities={"image", "video"}, features={"reference"}, max_reference_images=4
        ),
    )
    with sessions() as db:
        out = invoke(
            db,
            ctx(db, ids),
            context(rid, tasks[0]),
            "media.generate",
            args(references=[{"generation_id": ref["id"]}]),
            allow_charges=True,
        )
        assert out.status == "succeeded", out.model_dump()
        jid = uuid.UUID(out.data["job_id"])
        db.get(Generation, uuid.UUID(ref["id"])).output_blob_hash = other["expected_blob_hash"]
        db.commit()
    monkeypatch.setattr(
        MockProvider, "submit", lambda *a: pytest.fail("changed reference reached provider")
    )
    with sessions() as db:
        jobs.execute(db, jid)
    failed_without_output(sessions, jid)
