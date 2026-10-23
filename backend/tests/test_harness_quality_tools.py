"""Actual local media evidence, creative claims and human adoption remain distinct."""

# ruff: noqa: F811
import copy
import io
import json
import uuid

import pytest
from PIL import Image
from pydantic import ValidationError

from app.core.errors import Conflict, Forbidden
from app.models.engineering import EngineeringChange
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.models.media_index import MediaAnnotation
from app.models.work import CreativeWork
from app.modules.harness import quality_tools as quality
from app.modules.harness.context import ContextReference
from app.modules.harness.media_context import describe_media, inspect_media
from app.modules.harness.technical_validation import validate_output
from app.modules.tools.contracts import ToolContext
from tests.test_harness_context import context, fact, seed
from tests.test_media_workflow import changing_video, studio  # noqa: F401


def rows(db, ctx, requirements=None):
    run = ProductionRun(
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        request_key=uuid.uuid4(),
        goal="交付检查",
        status="running",
    )
    db.add(run)
    db.flush()
    task = ProductionTask(
        run_id=run.id,
        key="quality",
        goal="检查交付",
        status="running",
        specification={
            "quality_requirements": requirements
            or {"criteria": [{"id": "costume", "instruction": "主角红衣", "kind": "visual"}]}
        },
    )
    source = ProductionTask(run_id=run.id, key="observe", goal="检查媒体", status="succeeded")
    db.add_all([task, source])
    db.flush()
    task.dependencies = [str(source.id)]
    db.flush()
    return run, task, source


def image(db, ids, ctx, color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (80, 40), color).save(buf, "PNG")
    ref = seed(db, ids, buf.getvalue(), "image", "image/png", width=80, height=40)
    fixed = describe_media(db, ctx, ref)["reference"]
    assert validate_output(db, ctx, fixed)["status"] == "passed"
    return fixed


def receipt(db, task, tool, result, *, ident=None):
    task.attempts += 1
    data = copy.deepcopy(result)
    data.pop("content_parts", None)
    attempt = ProductionAttempt(
        id=ident or uuid.uuid4(),
        task_id=task.id,
        number=task.attempts,
        lease_token=uuid.uuid4(),
        status="succeeded",
        result={"status": "succeeded", "tool": tool, "data": data},
    )
    db.add(attempt)
    db.flush()
    return attempt


def report(refs, observations, *, criterion="costume", outcome="met", **kwargs):
    return {
        "artifacts": refs,
        "findings": [
            {
                "criterion_id": criterion,
                "outcome": outcome,
                "rationale": "已检查指定画面，保留人工判断空间",
                "observation_ids": [str(item.id) for item in observations],
            }
        ],
        **kwargs,
    }


def check(db, ctx, run, task, data):
    ident = uuid.uuid4()
    grant = ToolContext(
        input_context={
            "execution": {"run_id": str(run.id), "task_id": str(task.id), "request_key": str(ident)}
        }
    )
    result = quality.check(db, ctx, grant, quality.CheckArgs(report=data))
    attempt = receipt(db, task, "quality.check", result, ident=ident)
    return result, attempt


def test_real_image_report_and_parent_receipts_finish_without_adopting(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(db, ctx)
        ref = image(db, ids, ctx)
        observation = receipt(db, source, "media.inspect", inspect_media(db, ctx, ref))
        result, saved = check(db, ctx, run, task, report([ref], [observation]))
        assert result["ready"] and not result["adopted"]
        assert result["technical"]["ready"]
        assert result["creative_assessment"]["objective_score"] is None
        assert result["human_review"]["status"] == "pending"
        assert len(json.dumps(result, ensure_ascii=False)) < 23000
        done = quality.verify_quality(db, ctx, run, task, saved.id, [{"reference": ref}])
        assert done["ready"]
        assert done["creative_assessment"]["checks"][0]["observations"][0]["attempt_id"] == str(
            observation.id
        )
        assert not db.new and not db.dirty


def test_metadata_cannot_prove_visual_and_missing_or_negative_findings_do_not_pass(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(db, ctx)
        ref = image(db, ids, ctx)
        metadata = receipt(db, source, "media.inspect", describe_media(db, ctx, ref))
        result, saved = check(db, ctx, run, task, report([ref], [metadata]))
        assert not result["ready"]
        assert result["creative_assessment"]["checks"][0]["outcome"] == "not_checked"
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [ref])
        result, _ = check(db, ctx, run, task, {"artifacts": [ref]})
        assert not result["ready"]
        observation = receipt(db, source, "media.inspect", inspect_media(db, ctx, ref))
        result, _ = check(db, ctx, run, task, report([ref], [observation], outcome="not_met"))
        assert result["creative_assessment"]["checks"][0]["outcome"] == "not_met"
        assert not result["ready"]


def test_observation_permissions_revision_and_finish_identity_are_rechecked(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(db, ctx)
        ref, other = image(db, ids, ctx), image(db, ids, ctx, "blue")
        observed = receipt(db, source, "media.inspect", inspect_media(db, ctx, ref))
        _, saved = check(db, ctx, run, task, report([ref], [observed]))
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, uuid.uuid4(), [ref])
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [other])
        other_run, other_task, _ = rows(db, ctx)
        with pytest.raises(Conflict):
            check(db, ctx, other_run, other_task, report([ref], [observed]))
        source.status = "superseded"
        db.flush()
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [ref])
        source.status = "succeeded"
        task.specification = {
            "quality_requirements": {
                "criteria": [{"id": "costume", "instruction": "主角黑衣", "kind": "visual"}]
            }
        }
        db.flush()
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [ref])
        db.delete(ctx.membership)
        db.flush()
        with pytest.raises(Forbidden):
            quality.verify_quality(db, ctx, run, task, saved.id, [ref])


def test_crop_observation_cannot_cover_entire_picture_and_fixed_hash_is_checked(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(db, ctx)
        ref = image(db, ids, ctx)
        crop = {**ref, "region": {"x": 0.5, "y": 0, "width": 0.5, "height": 1}}
        observed = receipt(db, source, "media.inspect", inspect_media(db, ctx, crop))
        result, _ = check(db, ctx, run, task, report([ref], [observed]))
        assert not result["ready"]
        with pytest.raises(Conflict):
            check(db, ctx, run, task, report([{**ref, "expected_blob_hash": "a" * 64}], [observed]))


def test_video_samples_transcript_and_real_audio_have_distinct_coverage(studio, changing_video):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        criteria = [
            {"id": key, "instruction": key, "kind": kind, "coverage": coverage}
            for key, kind, coverage in [
                ("sample", "visual", "sampled"),
                ("full", "visual", "complete"),
                ("motion", "motion", "sampled"),
                ("voice", "audio", "complete"),
                ("words", "transcript", "complete"),
            ]
        ]
        run, task, source = rows(db, ctx, {"criteria": criteria})
        ref = seed(db, ids, changing_video, "video", "video/mp4", duration_ms=2000)
        fixed = describe_media(db, ctx, ref)["reference"]
        assert validate_output(db, ctx, fixed)["status"] == "passed"
        db.add(
            MediaAnnotation(
                version_id=uuid.UUID(ref["id"]),
                start_ms=0,
                end_ms=2000,
                kind="speech",
                text="你好",
                source={"method": "fixture"},
                created_by=ctx.user.id,
            )
        )
        db.flush()
        sampled = receipt(
            db, source, "media.inspect", inspect_media(db, ctx, fixed, frame_times_ms=[0, 1200])
        )
        data = {
            "artifacts": [fixed],
            "findings": [
                {
                    "criterion_id": item["id"],
                    "outcome": "met",
                    "rationale": "检查指定证据",
                    "observation_ids": [str(sampled.id)],
                }
                for item in criteria
            ],
        }
        result, _ = check(db, ctx, run, task, data)
        outcomes = {
            item["criterion_id"]: item["outcome"]
            for item in result["creative_assessment"]["checks"]
        }
        assert outcomes == {
            "sample": "met",
            "full": "not_checked",
            "motion": "not_checked",
            "voice": "not_checked",
            "words": "met",
        }
        audio = receipt(
            db, source, "media.inspect", inspect_media(db, ctx, fixed, include_audio=True)
        )
        data["findings"][3]["observation_ids"] = [str(audio.id)]
        result, _ = check(db, ctx, run, task, data)
        assert result["creative_assessment"]["checks"][3]["outcome"] == "met"
        assert not result["ready"]


def test_continuity_and_local_exception_are_bound_to_active_explicit_user_decision(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(
            db,
            ctx,
            {
                "criteria": [
                    {
                        "id": "continuity",
                        "instruction": "各镜头红衣一致",
                        "kind": "continuity",
                        "decision_ids": ["costume"],
                    }
                ]
            },
        )
        ref, other = image(db, ids, ctx), image(db, ids, ctx, "blue")
        observed = receipt(db, source, "media.inspect", inspect_media(db, ctx, ref))
        exception = fact(
            "local",
            "第二片段允许蓝衣",
            2,
            evidence={"quality_exception": {"criterion_id": "continuity", "reference": other}},
        )
        run.context = {"facts": [fact("costume", "全片红衣", 1), exception]}
        db.flush()
        data = report(
            [ref, other],
            [observed],
            criterion="continuity",
            local_exceptions=[
                {"criterion_id": "continuity", "artifact_index": 1, "decision_id": "local"}
            ],
        )
        result, saved = check(db, ctx, run, task, data)
        assert result["ready"]
        assert result["creative_assessment"]["checks"][0]["adjacent_pairs"] == [[0, 1]]
        exception["status"] = "withdrawn"
        run.context = {"facts": [fact("costume", "全片红衣", 1), exception]}
        db.flush()
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [ref, other])
        data["local_exceptions"] = []
        result, _ = check(db, ctx, run, task, data)
        assert not result["ready"]
        data["artifacts"] = [ref]
        result, _ = check(db, ctx, run, task, data)
        assert not result["ready"]


def test_human_review_is_real_fixed_candidate_state_and_never_an_agent_claim(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(db, ctx, {"require_human_review": True})
        ref = image(db, ids, ctx)
        work = CreativeWork(project_id=ctx.project.id, title="交付作品", created_by=ctx.user.id)
        db.add(work)
        db.flush()
        run.work_id = work.id
        change = EngineeringChange(
            work_id=work.id,
            created_by=ctx.user.id,
            request_key=uuid.uuid4(),
            fingerprint="f" * 64,
            base_revision=0,
            snapshot={},
            status="proposed",
        )
        db.add(change)
        db.flush()
        job = GenerationJob(
            project_id=ctx.project.id,
            target_id=uuid.UUID(ids["shot"]),
            provider="fixture",
            status="succeeded",
            input_snapshot={"engineering_change_id": str(change.id)},
        )
        db.add(job)
        db.flush()
        gen = Generation(
            project_id=ctx.project.id,
            job_id=job.id,
            target_id=uuid.UUID(ids["shot"]),
            provider="fixture",
            output_blob_hash=ref["expected_blob_hash"],
            output_type="image",
        )
        db.add(gen)
        db.flush()
        fixed = ContextReference(
            kind="generation", id=gen.id, expected_blob_hash=gen.output_blob_hash
        ).model_dump(mode="json")
        assert validate_output(db, ctx, fixed)["status"] == "passed"
        data = {
            "artifacts": [fixed],
            "candidate_id": str(change.id),
            "candidate_fingerprint": change.fingerprint,
        }
        result, saved = check(db, ctx, run, task, data)
        assert not result["ready"] and change.status == "proposed" and not gen.is_selected
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [fixed])
        # Represents the existing user-only engineering approval boundary, not quality.check.
        change.status = "accepted"
        change.committed_revision = 1
        db.flush()
        result = quality.verify_quality(db, ctx, run, task, saved.id, [fixed])
        assert result["ready"] and result["human_review"]["adopted"]
        with pytest.raises(Conflict):
            check(db, ctx, run, task, {**data, "artifacts": [ref]})
        change.fingerprint = "e" * 64
        db.flush()
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [fixed])


def test_schemas_reject_invented_score_duplicate_positions_and_boolean_indices():
    ref = {"kind": "generation", "id": str(uuid.uuid4()), "expected_blob_hash": "a" * 64}
    with pytest.raises(ValidationError):
        quality.QualityReport(artifacts=[ref], objective_score=99)
    with pytest.raises(ValidationError):
        quality.QualityReport(artifacts=[ref, ref])
    with pytest.raises(ValidationError):
        quality.Finding(
            criterion_id="test", outcome="met", rationale="test", artifact_indices=[True]
        )
    with pytest.raises(ValidationError):
        quality.QualityRequirements(
            criteria=[{"id": "same", "kind": "visual", "instruction": "x"}] * 2
        )


def test_mixed_delivery_criteria_follow_fixed_media_types_and_cannot_skip_outputs(
    studio, changing_video
):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(
            db,
            ctx,
            {
                "criteria": [
                    {
                        "id": "cover",
                        "instruction": "封面角色清晰",
                        "kind": "visual",
                        "media_types": ["image"],
                    },
                    {"id": "voice", "instruction": "试听配音", "kind": "audio"},
                ]
            },
        )
        cover = image(db, ids, ctx)
        clip = seed(db, ids, changing_video, "video", "video/mp4", duration_ms=2000)
        clip = describe_media(db, ctx, clip)["reference"]
        assert validate_output(db, ctx, clip)["status"] == "passed"
        cover_seen = receipt(db, source, "media.inspect", inspect_media(db, ctx, cover))
        clip_heard = receipt(
            db, source, "media.inspect", inspect_media(db, ctx, clip, include_audio=True)
        )
        data = {
            "artifacts": [cover, clip],
            "findings": [
                {
                    "criterion_id": "cover",
                    "outcome": "met",
                    "rationale": "封面已观察",
                    "artifact_indices": [0],
                    "observation_ids": [str(cover_seen.id)],
                },
                {
                    "criterion_id": "voice",
                    "outcome": "met",
                    "rationale": "视频音轨已试听",
                    "artifact_indices": [1],
                    "observation_ids": [str(clip_heard.id)],
                },
            ],
        }
        result, _ = check(db, ctx, run, task, data)
        assert result["ready"]
        data["findings"][1]["artifact_indices"] = [0]
        result, _ = check(db, ctx, run, task, data)
        assert not result["ready"]


def test_quality_without_full_technical_decode_is_unchecked(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(db, ctx)
        buf = io.BytesIO()
        Image.new("RGB", (90, 45), "green").save(buf, "PNG")
        ref = seed(db, ids, buf.getvalue(), "image", "image/png", width=90, height=45)
        ref = describe_media(db, ctx, ref)["reference"]
        observed = receipt(db, source, "media.inspect", inspect_media(db, ctx, ref))
        result, _ = check(db, ctx, run, task, report([ref], [observed]))
        assert result["creative_assessment"]["ready"]
        assert not result["technical"]["ready"] and not result["ready"]
        assert result["technical"]["checks"][0]["status"] == "not_checked"


def test_quality_cannot_borrow_unrelated_same_run_or_before_reopened_boundary(studio):
    from datetime import UTC, datetime, timedelta

    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        run, task, source = rows(db, ctx)
        ref = image(db, ids, ctx)
        observation = receipt(db, source, "media.inspect", inspect_media(db, ctx, ref))
        _, saved = check(db, ctx, run, task, report([ref], [observation]))
        task.dependencies = []
        task.state = {"preserved_results": [{"id": str(source.id), "result": source.result}]}
        db.flush()
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [ref])
        task.dependencies = [str(source.id)]
        source.state = {
            "evidence_invalid_before": (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
        }
        db.flush()
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [ref])
        source.state = {}
        task.state = {
            "evidence_invalid_before": (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
        }
        db.flush()
        with pytest.raises(Conflict):
            quality.verify_quality(db, ctx, run, task, saved.id, [ref])
