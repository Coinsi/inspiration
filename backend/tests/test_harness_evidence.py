"""Aggregate only reachable durable outputs; final-file selection is not a trust grant."""

# ruff: noqa: F811
import io
import uuid

import pytest
from PIL import Image
from pydantic import ValidationError

from app.core.errors import Conflict
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.modules.harness import runtime, schemas, service
from app.modules.harness.evidence import collect_receipts, evidence_tasks
from app.modules.harness.media_tools import observe_job
from app.modules.harness.technical_validation import validate_output
from app.modules.harness.verification import verify_output
from app.storage import cas
from tests.test_harness_context import context
from tests.test_harness_runtime import plan, registered_fixture_kernel, seeded  # noqa: F401
from tests.test_media_workflow import changing_video, studio  # noqa: F401


def produced(db, ctx, ids, *, raw=None, expected=None):
    if raw is None:
        data = io.BytesIO()
        Image.new("RGB", (80, 40), "red").save(data, "PNG")
        raw, kind, metadata = data.getvalue(), "image", {"width": 80, "height": 40}
    else:
        kind, metadata = "video", {"width": 320, "height": 180, "duration_ms": 2000}
    blob = cas.put_bytes(db, raw, "image/png" if kind == "image" else "video/mp4", **metadata)
    job = GenerationJob(
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        target_id=uuid.UUID(ids["shot"]),
        provider="fixture-local-encoded",
        status="succeeded",
        request_type=kind,
    )
    db.add(job)
    db.flush()
    gen = Generation(
        project_id=ctx.project.id,
        job_id=job.id,
        target_id=uuid.UUID(ids["shot"]),
        provider=job.provider,
        output_blob_hash=blob.hash,
        output_type=kind,
    )
    db.add(gen)
    db.flush()
    reference = {"kind": "generation", "id": str(gen.id), "expected_blob_hash": blob.hash}
    assert validate_output(db, ctx, reference, expected=expected)["status"] == "passed"
    receipt = {
        "tool": "media.job_observe",
        "status": "succeeded",
        "data": observe_job(db, ctx, job, check_storage=False),
    }
    return reference, receipt


def complete(task, receipt):
    task.status = "succeeded"
    task.state = {"steps": [{"receipt": receipt}], "job_receipt": receipt}
    task.result = {"receipts": [receipt]}


def nested(studio, *, requirements=None):
    sessions, ids, rid = seeded(
        studio,
        plan(("film", {"expected_output": "artifact", "output_requirements": requirements or {}})),
    )
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        parent = service.tasks(db, run)[0]
        service.add_plan(
            db,
            run,
            plan(
                ("cover", {"expected_output": "artifact"}),
                ("render", {"expected_output": "artifact"}),
            ),
            parent,
        )
        children = [row for row in service.tasks(db, run) if row.parent_id == parent.id]
        unrelated = ProductionTask(
            run_id=run.id, key="unrelated", goal="另一件事", status="succeeded"
        )
        db.add(unrelated)
        db.flush()
        db.commit()
        return sessions, ids, rid, parent.id, [row.id for row in children], unrelated.id


def test_parent_finish_selects_real_video_after_children_without_requiring_image_to_be_video(
    studio, changing_video
):
    requirements = {"has_video": True, "width": 320, "height": 180, "duration_ms": 2000}
    sessions, ids, rid, parent_id, children, _ = nested(studio, requirements=requirements)
    with sessions() as db:
        ctx = context(db, ids)
        image, image_receipt = produced(db, ctx, ids)
        video, video_receipt = produced(db, ctx, ids, raw=changing_video, expected=requirements)
        complete(db.get(ProductionTask, children[0]), image_receipt)
        complete(db.get(ProductionTask, children[1]), video_receipt)
        service.reconcile(db, db.get(ProductionRun, rid))
        db.commit()
        ticket = service.claim(db, rid)
        assert ticket["task_id"] == str(parent_id)
        db.commit()
        runtime.apply_action(
            db,
            ticket,
            schemas.FinishAction(kind="finish", message="完成最终视频", artifacts=[video]),
        )
        db.commit()
        task = db.get(ProductionTask, parent_id)
        assert task.status == "succeeded"
        assert [row["id"] for row in task.result["verified_outputs"]] == [video["id"]]
        assert (
            task.result["verified_outputs"][0]["technical_validation"]["expected"]["has_video"]
            is True
        )
        assert image["id"] not in {row["id"] for row in task.result["verified_outputs"]}


def test_finish_cannot_select_unrelated_or_invented_media_even_when_context_mentions_it(studio):
    sessions, ids, rid, parent_id, children, unrelated_id = nested(studio)
    with sessions() as db:
        ctx = context(db, ids)
        own, receipt = produced(db, ctx, ids)
        foreign, foreign_receipt = produced(db, ctx, ids)
        for child in children:
            complete(db.get(ProductionTask, child), receipt)
        complete(db.get(ProductionTask, unrelated_id), foreign_receipt)
        parent = db.get(ProductionTask, parent_id)
        parent.state = {
            **parent.state,
            "preserved_results": [
                {"id": str(unrelated_id), "result": {"receipts": [foreign_receipt]}}
            ],
        }
        run = db.get(ProductionRun, rid)
        service.reconcile(db, run)
        db.commit()
        ticket = service.claim(db, rid)
        db.commit()
        for selected in [
            foreign,
            {**own, "id": str(uuid.uuid4())},
            {**own, "expected_blob_hash": "a" * 64},
            {"id": own["id"]},
        ]:
            with pytest.raises(Conflict):
                runtime.apply_action(
                    db,
                    ticket,
                    schemas.FinishAction(kind="finish", message="不应接受", artifacts=[selected]),
                )
        assert parent.status == "running"
        runtime.apply_action(
            db,
            ticket,
            schemas.FinishAction(kind="finish", message="只交付本流程结果", artifacts=[own]),
        )
        db.commit()
        assert parent.status == "succeeded"


def test_default_aggregate_deduplicates_and_ignores_superseded_child_snapshots(studio):
    sessions, ids, rid, parent_id, children, unrelated_id = nested(studio)
    with sessions() as db:
        ctx = context(db, ids)
        current, receipt = produced(db, ctx, ids)
        replaced, old = produced(db, ctx, ids)
        complete(db.get(ProductionTask, children[0]), receipt)
        stale = db.get(ProductionTask, children[1])
        complete(stale, old)
        stale.status = "superseded"
        parent = db.get(ProductionTask, parent_id)
        parent.state = {
            **parent.state,
            "child_results": [{"id": str(stale.id), "result": {"receipts": [old]}}],
        }
        db.flush()
        run = db.get(ProductionRun, rid)
        receipts = collect_receipts(db, run, parent)
        verified = verify_output(db, ctx, "artifact", receipts)
        assert [row["id"] for row in verified] == [current["id"]]
        with pytest.raises(Conflict):
            verify_output(db, ctx, "artifact", receipts, artifact_refs=[replaced])
        # Same-run parent_id alone is insufficient: current declared children wins.
        extra = db.get(ProductionTask, unrelated_id)
        extra.parent_id = parent.id
        complete(extra, old)
        db.flush()
        assert extra.id not in {row.id for row in evidence_tasks(db, run, parent)}


def test_retained_old_revision_dependency_is_valid_but_unlinked_result_is_not(studio):
    sessions, ids, rid = seeded(
        studio,
        plan(
            ("source", {}),
            ("consumer", {"dependencies": ["source"], "expected_output": "artifact"}),
            ("other", {}),
        ),
    )
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        rows = {row.key: row for row in service.tasks(db, run)}
        ctx = context(db, ids)
        ref, receipt = produced(db, ctx, ids)
        complete(rows["source"], receipt)
        rows["consumer"].revision = 2
        run.revision = 2
        db.flush()
        assert rows["source"].id in {row.id for row in evidence_tasks(db, run, rows["consumer"])}
        assert (
            verify_output(db, ctx, "artifact", collect_receipts(db, run, rows["consumer"]))[0]["id"]
            == ref["id"]
        )
        rows["consumer"].dependencies = []
        rows["consumer"].state = {
            "preserved_results": [{"id": str(rows["source"].id), "result": rows["source"].result}]
        }
        db.flush()
        with pytest.raises(Conflict):
            verify_output(db, ctx, "artifact", collect_receipts(db, run, rows["consumer"]))


def test_creative_quality_requirements_only_apply_to_actual_artifact_delivery():
    quality = {"criteria": [{"id": "look", "instruction": "武器精美", "kind": "visual"}]}
    for expected in ["reply", "observation", "candidate"]:
        with pytest.raises(ValidationError):
            schemas.TaskSpec(
                key="invalid", goal="校验", expected_output=expected, quality_requirements=quality
            )
    assert schemas.TaskSpec(
        key="valid", goal="校验", expected_output="artifact", quality_requirements=quality
    )
