"""Plan preflight rejects unexecutable work without jobs, proposals or model calls."""

# ruff: noqa: F811
import copy
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.adapters.generation.mock import MockProvider
from app.core.errors import CapabilityUnsupported, Conflict, Forbidden, NotFound, QuotaExceeded
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership
from app.modules.generation import media_engine
from app.modules.harness import plan_contracts, schemas, service
from app.modules.tools import capabilities, registry
from app.storage import cas
from tests.test_harness_context import context
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_catalogue(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    monkeypatch.setattr(plan_contracts, "_PREFLIGHTS", dict(plan_contracts._PREFLIGHTS))


def run(ids, **policy):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.UUID(ids["project"]),
        created_by=ids["user"],
        conversation_id=None,
        work_id=None,
        context={"scope": [], "skills": []},
        policy=schemas.Policy(**policy).model_dump(mode="json"),
    )


def plan(name="project.overview", arguments=None, **kw):
    return schemas.Plan(
        summary="可执行计划",
        tasks=[
            schemas.TaskSpec(
                key="first",
                goal="实际完成一个操作",
                kind="tool",
                tool=name,
                arguments=arguments or {},
                **kw,
            )
        ],
    )


def media_args(**kw):
    return {"provider": "mock", "prompt": "一只杯子", "request_type": "image", **kw}


def source(db, ids, *, foreign=False):
    blob = cas.put_bytes(db, media_engine.mock_image(), "image/png", width=512, height=512)
    job = GenerationJob(
        project_id=ids["other"] if foreign else uuid.UUID(ids["project"]),
        target_type="shot",
        target_id=uuid.UUID(ids["shot"]),
        provider="mock",
        request_type="image",
        status="succeeded",
        created_by=ids["user"],
    )
    db.add(job)
    db.flush()
    generation = Generation(
        project_id=job.project_id,
        job_id=job.id,
        target_type="shot",
        target_id=job.target_id,
        provider="mock",
        output_type="image",
        output_blob_hash=blob.hash,
    )
    db.add(generation)
    db.flush()
    return generation, blob


def test_readonly_preflight_freezes_normalized_contract_without_invoking_handler(studio):
    _, sessions, ids = studio
    registry._HANDLERS["project.overview"] = lambda *_: pytest.fail("must never invoke a handler")
    with sessions() as db:
        current = run(ids)
        before = copy.deepcopy(current.context)
        frozen = plan_contracts.validate_plan(db, context(db, ids), current, plan())["first"]
        assert frozen["arguments"] == {}
        assert frozen["tool_versions"] == {"project.overview": 1}
        assert frozen["tool_contracts"]["project.overview"]["effect"] == "read"
        assert current.context == before
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        assert plan_contracts.assert_frozen_tool("project.overview", frozen).version == 1


def test_version_and_same_version_contract_drift_both_require_revalidation(studio):
    _, sessions, ids = studio
    with sessions() as db:
        frozen = plan_contracts.validate_plan(db, context(db, ids), run(ids), plan())["first"]
        original = registry.CATALOG["project.overview"]
        registry.CATALOG["project.overview"] = replace(original, version=original.version + 1)
        with pytest.raises(Conflict):
            plan_contracts.assert_frozen_tool("project.overview", frozen)
        registry.CATALOG["project.overview"] = original
        capabilities._POLICIES["project.overview"] = capabilities.ToolPolicy(
            effect="candidate",
            idempotency="proposal_only",
            cancellation="not_applicable",
            recovery="repeat_proposal",
            output_kind="changed_behavior",
        )
        with pytest.raises(Conflict):
            plan_contracts.assert_frozen_tool("project.overview", frozen)
        with pytest.raises(Conflict):
            plan_contracts.assert_frozen_tool("library.search", frozen)


def test_missing_tools_bad_arguments_and_effect_escalation_fail_before_dispatch(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        with pytest.raises(CapabilityUnsupported):
            plan_contracts.validate_plan(db, ctx, run(ids), plan("does.not.exist"))
        with pytest.raises(CapabilityUnsupported):
            plan_contracts.validate_plan(db, ctx, run(ids), plan(arguments={"unknown": "arg"}))
        with pytest.raises(Forbidden):
            plan_contracts.validate_plan(db, ctx, run(ids), plan("media.generate", media_args()))
        with pytest.raises(QuotaExceeded):
            plan_contracts.validate_plan(
                db,
                ctx,
                run(ids, effects=["read", "generate"]),
                plan("media.generate", media_args()),
            )
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_media_estimate_and_plan_budget_are_readonly_and_include_parallel_tasks(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        current = run(ids, effects=["read", "generate"], budget_points=15)
        direct = plan("media.generate", media_args(), expected_output="artifact")
        frozen = plan_contracts.validate_plan(db, ctx, current, direct)["first"]
        assert frozen["preflight"]["estimated_points"] == 10
        assert frozen["preflight"]["submitted"] is False
        assert frozen["arguments"]["count"] == 1
        parallel = schemas.Plan(
            summary="两张图",
            tasks=[
                *direct.tasks,
                direct.tasks[0].model_copy(update={"key": "second"}),
            ],
        )
        with pytest.raises(QuotaExceeded):
            plan_contracts.validate_plan(db, ctx, current, parallel)
        # A quote is read-only and does not require a generation grant or budget.
        quote = plan_contracts.validate_plan(
            db, ctx, run(ids), plan("media.estimate", media_args())
        )
        assert quote["first"]["preflight"]["estimated_points"] == 10
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_draft_plan_can_be_saved_without_budget_but_cannot_be_executed(studio):
    _, sessions, ids = studio
    with sessions() as db:
        result = service.create(
            db,
            context(db, ids),
            schemas.Create(
                goal="先估计做一张图",
                request_key=uuid.uuid4(),
                plan=plan("media.generate", media_args()),
            ),
        )
        assert result["status"] == "needs_plan_approval"
        assert result["tasks"] == []
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_reference_support_checked_and_fixed_hash_preserved_without_generation(studio, monkeypatch):
    _, sessions, ids = studio
    with sessions() as db:
        generation, blob = source(db, ids)
        ctx, current = context(db, ids), run(ids, effects=["read", "generate"], budget_points=100)
        requested = plan(
            "media.generate", media_args(references=[{"generation_id": str(generation.id)}])
        )
        with pytest.raises(CapabilityUnsupported):
            plan_contracts.validate_plan(db, ctx, current, requested)
        original = MockProvider.capabilities

        def reference_caps(self):
            caps = original(self)
            caps.features.add("reference")
            caps.max_reference_images = 1
            return caps

        monkeypatch.setattr(MockProvider, "capabilities", reference_caps)
        frozen = plan_contracts.validate_plan(db, ctx, current, requested)["first"]
        assert frozen["references"][0]["id"] == str(generation.id)
        assert frozen["references"][0]["expected_blob_hash"] == blob.hash
        assert frozen["references"][0]["required"]
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1  # only seeded source
        other, _ = source(db, ids, foreign=True)
        with pytest.raises(NotFound):
            plan_contracts.validate_plan(
                db,
                ctx,
                current,
                plan(
                    "media.generate",
                    media_args(references=[{"generation_id": str(other.id)}]),
                ),
            )


def test_fixed_task_references_and_agent_tool_subset_are_checked(studio):
    _, sessions, ids = studio
    with sessions() as db:
        generation, blob = source(db, ids)
        task = schemas.TaskSpec(
            key="inspect",
            goal="检查画面",
            tools=["media.inspect"],
            references=[{"kind": "generation", "id": str(generation.id)}],
        )
        frozen = plan_contracts.validate_plan(
            db, context(db, ids), run(ids), schemas.Plan(summary="检查", tasks=[task])
        )["inspect"]
        assert list(frozen["tool_versions"]) == ["media.inspect"]
        assert frozen["references"][0]["expected_blob_hash"] == blob.hash
        task.tools = ["missing.extension"]
        with pytest.raises(CapabilityUnsupported):
            plan_contracts.validate_plan(
                db, context(db, ids), run(ids), schemas.Plan(summary="检查", tasks=[task])
            )


def test_permission_and_target_scope_rechecked_even_when_plan_was_prepared_earlier(studio):
    _, sessions, ids = studio
    with sessions() as db:
        current = run(ids)
        current.context["scope"] = [{"target_type": "shot", "target_id": ids["shot"]}]
        edit = plan(
            "object.propose_edit",
            {
                "target_type": "shot",
                "target_id": str(uuid.uuid4()),
                "ops": [{"op": "set_field", "field": "title", "value": "清晨"}],
                "summary": "改标题",
            },
        )
        with pytest.raises(Forbidden):
            plan_contracts.validate_plan(db, context(db, ids), current, edit)
        membership = db.scalar(
            select(Membership).where(Membership.project_id == current.project_id)
        )
        membership.role = "viewer"
        db.flush()
        with pytest.raises(Forbidden):
            plan_contracts.validate_plan(
                db, context(db, ids), run(ids), plan("media.estimate", media_args())
            )
