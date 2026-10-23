"""Durable resource accounting and real job output boundaries, without paid APIs."""

# ruff: noqa: F811
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.errors import QuotaExceeded
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.modules.generation import jobs, media_engine, video_tools
from app.modules.harness import kernel, resources, runtime, schemas
from app.storage import cas
from tests.test_harness_context import context
from tests.test_harness_runtime import command, seeded
from tests.test_media_workflow import studio  # noqa: F401


def lock_run(db, rid):
    return db.scalar(
        select(ProductionRun)
        .where(ProductionRun.id == rid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def test_restart_keeps_unknown_reservation_nonzero_and_measured_difference_is_reusable(studio):
    sessions, _, rid = seeded(studio, max_model_tokens=100)
    with sessions() as db:
        run = lock_run(db, rid)
        resources.reserve_units(run, "first", {"model_tokens": 80})
        db.commit()
    with sessions() as db:
        run = lock_run(db, rid)
        resources.reserve_units(run, "first", {"model_tokens": 80})
        resources.settle_units(run, "first", {})
        assert resources.summary(run)["model_tokens"] == {
            "measured": 0,
            "reserved_or_unknown": 80,
            "limit": 100,
            "remaining": 20,
        }
        with pytest.raises(QuotaExceeded):
            resources.reserve_units(run, "second", {"model_tokens": 21})
        resources.settle_units(run, "first", {"model_tokens": 30})
        resources.reserve_units(run, "second", {"model_tokens": 70})
        db.commit()
    with sessions() as db:
        state = resources.summary(db.get(ProductionRun, rid))["model_tokens"]
        assert state == {"measured": 30, "reserved_or_unknown": 70, "limit": 100, "remaining": 0}


@pytest.mark.parametrize(
    "usage", [None, {}, {"total_tokens": True}, {"total_tokens": -1}, {"total_tokens": "20"}]
)
def test_missing_or_invalid_provider_usage_never_refunds_reserved_tokens(studio, usage):
    sessions, _, rid = seeded(studio)
    with sessions() as db:
        run = lock_run(db, rid)
        ticket = {"attempt_id": str(uuid.uuid4())}
        resources.reserve_model(run, ticket, {"goal": "镜头"}, [])
        before = resources.summary(run)["model_tokens"]["reserved_or_unknown"]
        assert before > 0
        resources.settle_model(run, ticket, usage)
        db.commit()
    with sessions() as db:
        state = resources.summary(db.get(ProductionRun, rid))["model_tokens"]
        assert state["measured"] == 0 and state["reserved_or_unknown"] == before


def test_concurrent_workers_lock_shared_run_and_cannot_overreserve(studio):
    sessions, _, rid = seeded(studio, max_output_storage_bytes=100, max_media_transfer_bytes=100)
    barrier = Barrier(4)

    def reserve(index):
        with sessions() as db:
            barrier.wait(timeout=5)
            run = lock_run(db, rid)
            try:
                resources.reserve_units(
                    run, str(index), {"output_storage_bytes": 60, "media_transfer_bytes": 60}
                )
                db.commit()
                return True
            except QuotaExceeded:
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(4)))
    assert sum(results) == 1
    with sessions() as db:
        state = resources.summary(db.get(ProductionRun, rid))
        assert state["output_storage_bytes"]["remaining"] == 40
        assert state["media_transfer_bytes"]["reserved_or_unknown"] == 60


def test_measured_media_size_releases_difference_only_for_matching_job(studio):
    sessions, ids, rid = seeded(
        studio, max_output_storage_bytes=1000, max_media_transfer_bytes=1000
    )
    with sessions() as db:
        job = mock_job(db, ids)
        first = cas.put_bytes(db, b"first-output", "application/octet-stream")
        second = cas.put_bytes(db, b"second-output", "application/octet-stream")
        for blob in (first, second):
            db.add(
                Generation(
                    project_id=job.project_id,
                    job_id=job.id,
                    target_type=job.target_type,
                    target_id=job.target_id,
                    provider="mock",
                    output_type="image",
                    output_blob_hash=blob.hash,
                )
            )
        run = lock_run(db, rid)
        resources.reserve_units(
            run, "media", {"output_storage_bytes": 800, "media_transfer_bytes": 800}, job_id=job.id
        )
        resources.reserve_units(
            run,
            "unknown",
            {"output_storage_bytes": 100, "media_transfer_bytes": 100},
            job_id=uuid.uuid4(),
        )
        db.flush()
        resources.settle_media(db, run, job.id)
        db.commit()
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        for unit in ("output_storage_bytes", "media_transfer_bytes"):
            assert resources.summary(run)[unit] == {
                "measured": 25,
                "reserved_or_unknown": 100,
                "limit": 1000,
                "remaining": 875,
            }


def test_limits_update_preserves_pause_and_cannot_change_effects_or_points(studio):
    sessions, ids, rid = seeded(studio, budget_points=50, effects=["read", "candidate"])
    with sessions() as db:
        command(db, ids, rid, "pause")
        db.commit()
        command(db, ids, rid, "update_limits", limits={"max_model_tokens": 5000})
        db.commit()
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        assert run.status == "paused"
        assert run.policy["max_model_tokens"] == 5000
        assert run.policy["budget_points"] == 50 and run.policy["effects"] == ["read", "candidate"]
        for changes in ({"budget_points": 5000}, {"effects": 1}):
            with pytest.raises(ValidationError):
                schemas.Control(
                    request_key=uuid.uuid4(),
                    expected_revision=run.revision,
                    action="update_limits",
                    limits=changes,
                )


def test_actual_overrun_is_recorded_and_stops_next_resource_operation(studio):
    sessions, _, rid = seeded(studio, max_model_tokens=100)
    with sessions() as db:
        run = lock_run(db, rid)
        resources.reserve_units(run, "model", {"model_tokens": 80})
        resources.settle_units(run, "model", {"model_tokens": 120})
        db.commit()
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        assert resources.summary(run)["model_tokens"]["measured"] == 120
        with pytest.raises(QuotaExceeded):
            resources.ensure_limits(run)


def test_raising_action_limit_resumes_only_exhausted_step_without_resetting_usage(studio):
    from app.modules.harness import service

    sessions, ids, rid = seeded(studio, max_actions=1)
    with sessions() as db:
        first = service.claim(db, rid)
        service.finish_attempt(db, first, status="queued", result={"kept": "first action"})
        db.commit()
        assert service.claim(db, rid) is None
        task = db.get(ProductionTask, uuid.UUID(first["task_id"]))
        assert task.status == "failed" and task.error["code"] == "ACTION_LIMIT"
        db.commit()
        command(db, ids, rid, "update_limits", limits={"max_actions": 1})
        assert task.status == "failed"
        db.commit()
        command(db, ids, rid, "update_limits", limits={"max_actions": 2})
        assert task.status == "queued" and task.attempts == 1
        assert task.result == {"kept": "first action"}
        db.commit()
        second = service.claim(db, rid)
        assert second and task.attempts == 2
        service.finish_attempt(
            db,
            second,
            status="failed",
            error={"code": "NO_PROGRESS", "message": "Needs guidance", "retryable": False},
        )
        db.commit()
        command(db, ids, rid, "update_limits", limits={"max_actions": 3})
        assert task.status == "failed" and task.error["code"] == "NO_PROGRESS"
        assert task.attempts == 2
        db.commit()


def test_multimodal_observation_transfer_is_reserved_before_model_call(studio):
    sessions, _, rid = seeded(studio, max_media_transfer_bytes=100)
    media = [{"type": "input_audio", "input_audio": {"data": "A" * 128, "format": "wav"}}]
    with sessions() as db:
        run = lock_run(db, rid)
        ticket = {"attempt_id": str(uuid.uuid4())}
        with pytest.raises(QuotaExceeded):
            resources.reserve_model(run, ticket, {"goal": "观察音频"}, media)
        assert run.context.get("resource_reservations", {}) == {}
        run.policy = {**run.policy, "max_media_transfer_bytes": 1000}
        resources.reserve_model(run, ticket, {"goal": "观察音频"}, media)
        resources.settle_model(run, ticket, {"total_tokens": 20})
        db.commit()
    with sessions() as db:
        summary = resources.summary(db.get(ProductionRun, rid))
        assert summary["model_tokens"]["measured"] == 20
        assert summary["media_transfer_bytes"]["reserved_or_unknown"] == len(
            json.dumps(media).encode()
        )


def test_preview_budget_rejection_is_atomic_and_repeat_must_match_output(studio):
    sessions, _, rid = seeded(studio, max_output_storage_bytes=1)
    with sessions() as db:
        run = lock_run(db, rid)
        with pytest.raises(QuotaExceeded):
            resources.reserve_preview(db, run, {"attempt_id": "test"}, {"change_id": "one"})
        assert run.context.get("preview_reservations", {}) == {}
        assert run.context.get("resource_reservations", {}) == {}
        run.policy = {**run.policy, "max_output_storage_bytes": 1024**3}
        resources.reserve_preview(db, run, {"attempt_id": "test"}, {"change_id": "one"})
        with pytest.raises(QuotaExceeded):
            resources.reserve_preview(db, run, {"attempt_id": "test"}, {"change_id": "two"})


@pytest.mark.parametrize("envelope", [False, True])
def test_runtime_accepts_legacy_kernel_and_usage_envelope_with_durable_accounting(
    studio, monkeypatch, envelope
):
    sessions, _, rid = seeded(studio)
    action = {"kind": "finish", "message": "测试对话完成"}
    result = kernel.DecisionResult(action, {"total_tokens": 42}) if envelope else action
    monkeypatch.setitem(kernel._KERNELS, "fixture", lambda *_: result)
    from app.modules.harness import service

    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions)
    with sessions() as db:
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert task.status == "succeeded", task.error
        run = db.get(ProductionRun, rid)
        entry = run.context["resource_reservations"]["model:" + ticket["attempt_id"]]
        assert entry["reserved"]["model_tokens"] > 0
        if envelope:
            assert entry["actual"] == {"model_tokens": 42}
        else:
            assert entry["status"] == "unknown" and not entry["actual"]


def test_cloud_usage_envelope_keeps_legacy_chat_call_compatible(monkeypatch):
    from app.adapters.assist import cloud_llm

    seen = []
    text = json.dumps({"kind": "finish", "message": "完成"})
    body = json.dumps(
        {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 77}}
    ).encode()

    def response(request, timeout):
        seen.append(json.loads(request.data))
        return io.BytesIO(body)

    monkeypatch.setattr(cloud_llm.urllib.request, "urlopen", response)
    assert cloud_llm._chat("system", "user", "https://fixture.invalid/v1", "test", "mock") == text
    action, usage = kernel.decide_with_usage(
        "cloud_llm",
        {"base_url": "https://fixture.invalid/v1", "api_key": "test", "model": "mock"},
        {"policy": {"max_model_output_tokens": 512}},
    )
    assert action.kind == "finish" and usage == {"total_tokens": 77}
    assert "max_completion_tokens" not in seen[0]
    assert seen[1]["max_completion_tokens"] == 512


def mock_job(db, ids, snapshot=None):
    job = GenerationJob(
        project_id=uuid.UUID(ids["project"]),
        created_by=ids["user"],
        target_type="shot",
        target_id=uuid.UUID(ids["shot"]),
        provider="mock",
        request_type="image",
        status="pending",
        estimated_cost=0,
        params={},
        input_snapshot={"request_type": "image", "prompt": "mock", "count": 2, **(snapshot or {})},
    )
    db.add(job)
    db.flush()
    return job


def test_oversized_provider_batch_writes_no_partial_cas_results(studio, monkeypatch):
    _, sessions, ids = studio
    first, second = media_engine.mock_image(0), media_engine.mock_image(1)
    cap = max(len(first), len(second)) + 1
    assert cap < len(first) + len(second)
    with sessions() as db:
        job = mock_job(db, ids, {"harness_resource_limits": {"output_bytes": cap}})
        job_id = job.id
        db.commit()
    writes = []
    monkeypatch.setattr(cas, "put_bytes", lambda *_a, **_k: writes.append("bytes"))
    monkeypatch.setattr(cas, "put_file", lambda *_a, **_k: writes.append("file"))
    with sessions() as db:
        jobs.execute(db, job_id)
    with sessions() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "failed" and "总大小超过" in job.error
        assert writes == []
        assert db.scalar(select(func.count()).select_from(Generation)) == 0


def test_invalid_persisted_cap_stops_before_any_provider_request(studio, monkeypatch):
    from app.adapters.generation.mock import MockProvider

    _, sessions, ids = studio
    with sessions() as db:
        job = mock_job(db, ids, {"harness_resource_limits": {"output_bytes": True}})
        job_id = job.id
        db.commit()
    submissions = []
    monkeypatch.setattr(MockProvider, "submit", lambda *_: submissions.append(True))
    with sessions() as db:
        jobs.execute(db, job_id)
    with sessions() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "failed" and "限制无效" in job.error
        assert submissions == []


def test_oversized_local_path_batch_writes_no_partial_cas_results(studio, monkeypatch, tmp_path):
    _, sessions, ids = studio
    paths = [tmp_path / "first.png", tmp_path / "second.png"]
    for path in paths:
        path.write_bytes(media_engine.mock_image())
    with sessions() as db:
        blob = cas.put_bytes(db, b"fixture-input", "application/octet-stream")
        job = jobs.local_job(
            db,
            context(db, ids),
            "shot",
            uuid.UUID(ids["shot"]),
            "video_frames",
            {
                "source_blob": blob.hash,
                "options": {},
                "harness_resource_limits": {"output_bytes": paths[0].stat().st_size + 1},
            },
            "image",
            defer=True,
        )
        job_id = job.id
        db.commit()

    @contextmanager
    def process(*_):
        yield [(path, "image", {}) for path in paths]

    monkeypatch.setattr(video_tools, "process_files", process)
    writes = []
    monkeypatch.setattr(cas, "put_bytes", lambda *_a, **_k: writes.append("bytes"))
    monkeypatch.setattr(cas, "put_file", lambda *_a, **_k: writes.append("file"))
    with sessions() as db:
        jobs.execute(db, job_id)
    with sessions() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "failed" and "总大小超过" in job.error
        assert writes == []
        assert db.scalar(select(func.count()).select_from(Generation)) == 0


def test_unmarked_legacy_job_retains_existing_output_behavior(studio):
    _, sessions, ids = studio
    with sessions() as db:
        job = mock_job(db, ids)
        job_id = job.id
        db.commit()
        jobs.execute(db, job_id)
    with sessions() as db:
        assert db.get(GenerationJob, job_id).status == "succeeded"
        assert db.scalar(select(func.count()).select_from(Generation)) == 2


def test_generate_and_process_snapshots_keep_their_reserved_output_caps(studio, monkeypatch):
    from app.modules.harness import media_tools, process_tools
    from tests.test_harness_editing_tools import png, source, work
    from tests.test_harness_media_tools import context as tool_context
    from tests.test_harness_media_tools import setup_run

    _, sessions, ids = studio
    work_id = work(studio)
    ref = source(sessions, ids, png("red"), library=True)
    rid, task_ids = setup_run(sessions, ids, task_count=2)

    def defer(db, job):
        db.commit()
        return job

    monkeypatch.setattr(jobs, "dispatch", defer)
    with sessions() as db:
        run = lock_run(db, rid)
        run.work_id = uuid.UUID(work_id)
        run.policy = {**run.policy, "effects": ["read", "candidate", "generate"]}
        db.commit()
        generated_ctx = tool_context(rid, task_ids[0])
        generated_ctx.work_id = run.work_id
        generated = media_tools.generate(
            db,
            context(db, ids),
            generated_ctx,
            media_tools.GenerateArgs(provider="mock", prompt="双候选", count=2),
        )
        generated_job = db.get(GenerationJob, uuid.UUID(generated["job_id"]))
        generated_cap = generated_job.input_snapshot["harness_resource_limits"]
        assert generated_cap["output_bytes"] == 2 * 256 * 1024 * 1024
        processed_ctx = tool_context(rid, task_ids[1])
        processed_ctx.work_id = run.work_id
        args = process_tools.ProcessArgs(
            work_id=work_id, source=ref, operation={"mode": "image.adjust"}
        )
        expected = process_tools.preflight(db, context(db, ids), processed_ctx, args)[
            "reserved_output_bytes"
        ]
        processed = process_tools.process(db, context(db, ids), processed_ctx, args)
        processed_job = db.get(GenerationJob, uuid.UUID(processed["job_id"]))
        processed_cap = processed_job.input_snapshot["harness_resource_limits"]
        assert processed_cap["output_bytes"] == expected
        db.commit()
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        for limit in (generated_cap, processed_cap):
            entry = run.context["resource_reservations"][limit["reservation_key"]]
            assert entry["reserved"]["output_storage_bytes"] == limit["output_bytes"]
            assert entry["job_id"]


def test_engineering_preview_freezes_cap_in_job_snapshot_before_dispatch(studio, monkeypatch):
    from app.modules.engineering import tools as engineering_tools
    from tests.test_engineering import setup
    from tests.test_harness_engineering import propose, put
    from tests.test_harness_media_tools import context as tool_context
    from tests.test_harness_media_tools import setup_run

    client, sessions, ids, root, wid, _ = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    candidate = propose(client, path, 0, [put()])
    rid, task_ids = setup_run(sessions, ids)
    seen = []

    def defer(db, job):
        seen.append(job.input_snapshot.copy())
        db.commit()
        return job

    monkeypatch.setattr(jobs, "dispatch", defer)
    with sessions() as db:
        run = lock_run(db, rid)
        run.work_id = uuid.UUID(wid)
        ctx = tool_context(rid, task_ids[0])
        ctx.work_id = run.work_id
        args = engineering_tools.Preview(
            work_id=wid, change_id=candidate["id"], fingerprint=candidate["fingerprint"]
        )
        resources.reserve_preview(
            db,
            run,
            {"attempt_id": ctx.input_context["execution"]["request_key"]},
            args.model_dump(mode="json"),
        )
        db.commit()
        result = engineering_tools.preview(db, context(db, ids), ctx, args)
        job = db.get(GenerationJob, uuid.UUID(str(result["preview_job_id"])))
        limits = job.input_snapshot["harness_resource_limits"]
        assert limits["output_bytes"] == 512 * 1024 * 1024
        assert seen[0]["harness_resource_limits"] == limits
        assert job.input_snapshot["harness_origin"]["run_id"] == str(rid)


@pytest.mark.parametrize("mismatched_reservation", [False, True])
def test_lost_preview_receipt_rebinds_original_resource_reservation_before_settlement(
    studio, mismatched_reservation
):
    from app.modules.harness.job_wait import recover_uncertain
    from tests.test_engineering_review import draft, preview, ready
    from tests.test_harness_media_tools import setup_run

    client, sessions, ids, path, tid, url = ready(studio)
    run_id, task_ids = setup_run(sessions, ids)
    candidate = draft(client, path, tid, url)
    rendered = preview(client, path, candidate)
    job_id = uuid.UUID(rendered["preview_job_id"])
    with sessions() as db:
        run = lock_run(db, run_id)
        run.work_id = uuid.UUID(path.split("/")[-2])
        run.policy = {**run.policy, "max_output_storage_bytes": 512 * 1024 * 1024}
        task = db.get(ProductionTask, task_ids[0])
        task.status = "uncertain"
        arguments = {
            "work_id": str(run.work_id),
            "change_id": candidate["id"],
            "fingerprint": candidate["fingerprint"],
        }
        attempt = ProductionAttempt(
            task_id=task.id,
            number=1,
            lease_token=task.lease_token,
            status="uncertain",
            operation={"phase": "tool", "tool": "engineering.preview", "arguments": arguments},
            result={},
            error={},
        )
        db.add(attempt)
        db.flush()
        resources.reserve_preview(db, run, {"attempt_id": str(attempt.id)}, arguments)
        key = "preview:" + str(attempt.id)
        if mismatched_reservation:
            run.context = {
                **run.context,
                "preview_reservations": {
                    str(attempt.id): {"change_id": str(uuid.uuid4()), "output_id": None}
                },
            }
        db.commit()
    with sessions() as db:
        run = lock_run(db, run_id)
        recovered = recover_uncertain(db, run)
        if mismatched_reservation:
            assert recovered == []
            assert db.get(ProductionTask, task_ids[0]).status == "uncertain"
            assert run.context["resource_reservations"][key]["job_id"] is None
        else:
            assert recovered[0]["job_id"] == str(job_id)
            assert run.context["resource_reservations"][key]["job_id"] == str(job_id)
            resources.settle_media(db, run, job_id)
            state = resources.summary(run)["output_storage_bytes"]
            assert 0 < state["measured"] < 512 * 1024 * 1024
            assert state["reserved_or_unknown"] == 0
            resources.reserve_units(run, "next", {"output_storage_bytes": state["remaining"]})
        db.commit()
