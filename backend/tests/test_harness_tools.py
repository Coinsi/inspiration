"""Shared tool contracts exercised without an AgentRun or model execution."""

# ruff: noqa: F811
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.agent import AgentRun
from app.models.engineering import EngineeringChange, EngineeringDocument
from app.models.enums import Role
from app.models.generation import GenerationJob, Quota
from app.models.identity import Membership, Project, User
from app.models.shot import Shot
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy
from app.modules.tools.contracts import ToolContext
from app.modules.tools.invocation import invoke, prepare_generation
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def context(db, ids):
    # Deliberately no AgentRun, and a cached role which invocation must not trust.
    return SimpleNamespace(
        project=db.get(Project, uuid.UUID(ids["project"])),
        user=db.get(User, ids["user"]),
        role=Role.admin,
    )


def selected(ids, **kwargs):
    return ToolContext(scope=[{"target_type": "shot", "target_id": ids["shot"]}], **kwargs)


def proposal(work_id):
    return {
        "work_id": work_id,
        "request_key": str(uuid.uuid4()),
        "base_revision": 0,
        "commands": [
            {
                "operation": "document.put",
                "document_id": str(uuid.uuid4()),
                "expected_revision": 0,
                "kind": "image.document",
                "title": "工具草稿",
                "payload": {"canvas": {"width": 16, "height": 16}, "layers": []},
            }
        ],
    }


def test_catalogue_declares_real_effect_and_filters_on_demand():
    result = capabilities.catalogue(group="engineering", effects=["candidate", "preview"])
    assert {"engineering.propose", "engineering.preview", "engineering.compose"} <= {
        x["name"] for x in result
    }
    assert all(x["effect"] in {"candidate", "preview"} for x in result)
    candidate = capabilities.get_capability("engineering.propose")
    assert candidate.effect == "candidate" and not candidate.requires_review
    assert candidate.policy.idempotency == "request_key"
    generation = capabilities.get_capability("generation.request")
    assert generation.effect == "generate" and generation.requires_review
    assert not generation.policy.charges_on_invoke and not generation.policy.starts_job
    assert not capabilities.get_capability("engineering.preview", context=ToolContext()).available
    assert capabilities.catalogue(names=[]) == []
    assert capabilities.catalogue(names=["missing"]) == []
    json.dumps(result)


def test_extensions_fail_closed_until_explicit_contract(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    registry.register(
        registry.Tool("test.render", "render", registry.Empty, group="test", version=3),
        lambda *args: {"ok": True},
    )
    unknown = capabilities.get_capability("test.render")
    assert unknown.effect is None and not unknown.available
    policy = ToolPolicy(
        effect="preview",
        permissions=("engineering.edit",),
        idempotency="none",
        cancellation="unsupported",
        recovery="manual",
        output_kind="render_result",
    )
    with pytest.raises(ValueError, match="registered permissions"):
        capabilities.register_contract(
            "test.render", policy.model_copy(update={"permissions": ("unregistered.action",)})
        )
    capabilities.register_contract("test.render", policy)
    known = capabilities.get_capability("test.render")
    assert known.available and known.version == 3 and known.effect == "preview"
    with pytest.raises(ValueError, match="already registered"):
        capabilities.register_contract("test.render", policy)


def test_standalone_proposal_is_idempotent_not_adopted(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    work = post_ok(client, root + "/works", {"title": "独立工具作品"})
    args = proposal(work["id"])
    task = selected(ids, work_id=uuid.UUID(work["id"]))
    with sessions() as db:
        ctx = context(db, ids)
        read_only = invoke(db, ctx, task, "engineering.propose", args, allowed_effects=["read"])
        assert read_only.status == "failed" and read_only.error.code == "FORBIDDEN"
        wrong = invoke(db, ctx, selected(ids), "engineering.propose", args)
        assert wrong.status == "failed"
        first = invoke(db, ctx, task, "engineering.propose", args, expected_version=1)
        assert first.status == "succeeded", first.model_dump()
        assert not first.adopted
        assert first.recovery["request_key"] == args["request_key"]
        again = invoke(db, ctx, task, "engineering.propose", args)
        assert again.data["id"] == first.data["id"]
        assert db.scalar(select(func.count()).select_from(EngineeringChange)) == 1
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
        db.commit()
    assert client.get(root + f"/works/{work['id']}/engineering").json()["revision"] == 0


def test_object_edit_returns_review_only_and_rechecks_scope(studio):
    _, sessions, ids = studio
    args = {
        "target_type": "shot",
        "target_id": ids["shot"],
        "summary": "新的标题建议",
        "ops": [{"op": "set_field", "field": "title", "value": "夜景"}],
    }
    with sessions() as db:
        ctx = context(db, ids)
        outside = ToolContext(scope=[{"target_type": "shot", "target_id": str(uuid.uuid4())}])
        denied = invoke(db, ctx, outside, "object.propose_edit", args)
        assert denied.status == "failed" and denied.error.category == "authorization"
        result = invoke(db, ctx, selected(ids), "object.propose_edit", args)
        assert result.status == "review_required" and result.review["tool"] == "object.propose_edit"
        assert result.data["after"]["fields"]["title"] == "夜景"
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "Test shot"
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
        json.dumps(result.model_dump(mode="json"))


def test_generation_preparation_never_creates_jobs_or_charges(studio):
    _, sessions, ids = studio
    args = {
        "target_type": "shot",
        "target_id": ids["shot"],
        "provider": "mock",
        "request_type": "image",
        "prompt": "夜景",
        "count": 2,
    }
    with sessions() as db:
        result = prepare_generation(db, context(db, ids), selected(ids), args)
        assert result.status == "review_required", result.model_dump()
        assert result.data["estimated_points"] >= 0
        assert result.effect == "generate" and not result.adopted
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
        assert all(q.used_cost == 0 for q in db.scalars(select(Quota)))
        assert db.get(Shot, uuid.UUID(ids["shot"])).selected_generation_id is None


def test_version_invalid_input_and_current_membership(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        mismatch = invoke(db, ctx, selected(ids), "project.overview", {}, expected_version=99)
        assert mismatch.status == "failed" and mismatch.error.category == "conflict"
        invalid = invoke(db, ctx, selected(ids), "object.read", {"target_type": "anything"})
        assert invalid.status == "failed" and not invalid.error.retryable
        missing = invoke(db, ctx, selected(ids), "unknown.tool", {})
        assert missing.status == "failed" and missing.error.code == "CAPABILITY_UNSUPPORTED"
        member = db.scalar(select(Membership).where(Membership.project_id == ctx.project.id))
        member.role = "viewer"
        db.flush()
        args = {
            "target_type": "shot",
            "target_id": ids["shot"],
            "summary": "change",
            "ops": [{"op": "set_field", "field": "title", "value": "forbidden"}],
        }
        denied = invoke(db, ctx, selected(ids), "object.propose_edit", args)
        assert denied.status == "failed" and denied.error.code == "FORBIDDEN"
        db.delete(member)
        db.flush()
        revoked = invoke(db, ctx, selected(ids), "project.overview", {})
        assert revoked.status == "failed" and revoked.error.code == "FORBIDDEN"


def test_wrong_project_is_rejected_even_with_valid_context(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        ctx.project = db.get(Project, ids["other"])
        result = invoke(
            db, ctx, selected(ids), "object.read", {"target_type": "shot", "target_id": ids["shot"]}
        )
        assert result.status == "failed" and result.error.category == "authorization"


def test_skills_use_selected_fixed_snapshot_without_agent(studio):
    _, sessions, ids = studio
    skill_id = str(uuid.uuid4())
    task = selected(
        ids,
        skill_snapshot=[
            {
                "id": skill_id,
                "revision": 3,
                "instructions": "保留人物眼神",
                "files": [],
            }
        ],
    )
    with sessions() as db:
        result = invoke(
            db, context(db, ids), task, "skill.read_file", {"id": skill_id, "path": "SKILL.md"}
        )
        assert result.status == "succeeded" and result.data["revision"] == 3
        assert result.data["content"] == "保留人物眼神"
        outside = invoke(db, context(db, ids), task, "skill.load", {"id": str(uuid.uuid4())})
        assert outside.status == "failed" and outside.error.category == "authorization"


def test_failed_tool_output_rolls_back_only_its_write(studio, monkeypatch):
    _, sessions, ids = studio
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))

    def broken(db, ctx, task, args):
        db.get(Shot, uuid.UUID(ids["shot"])).title = "partial write"
        db.flush()
        return {"unexpected": True}

    registry.register(registry.Tool("test.broken", "broken", registry.Empty), broken)
    capabilities.register_contract(
        "test.broken",
        ToolPolicy(
            effect="candidate",
            permissions=("shot.edit",),
            idempotency="none",
            cancellation="not_applicable",
            recovery="manual",
            output_kind="candidate",
            output_schema={"type": "object", "required": ["candidate_id"]},
        ),
    )
    with sessions() as db:
        ctx = context(db, ids)
        ctx.project.name = "caller-owned change"
        db.flush()
        result = invoke(db, ctx, selected(ids), "test.broken", {})
        assert result.status == "failed" and result.error.code == "INVALID_TOOL_OUTPUT"
        assert not result.error.retryable
        assert db.get(Shot, uuid.UUID(ids["shot"])).title == "Test shot"
        assert ctx.project.name == "caller-owned change"


def test_partial_candidate_failure_leaves_no_draft_documents(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    work = post_ok(client, root + "/works", {"title": "原子候选"})
    args = proposal(work["id"])
    args["commands"].append(
        {
            "operation": "resource.add",
            "usage_id": str(uuid.uuid4()),
            "source_kind": "library",
            "source_id": str(uuid.uuid4()),
            "name": "missing source",
        }
    )
    with sessions() as db:
        before = db.scalar(select(func.count()).select_from(EngineeringDocument))
        result = invoke(
            db,
            context(db, ids),
            selected(ids, work_id=uuid.UUID(work["id"])),
            "engineering.propose",
            args,
        )
        assert result.status == "failed"
        assert db.scalar(select(func.count()).select_from(EngineeringDocument)) == before
        assert db.scalar(select(func.count()).select_from(EngineeringChange)) == 0


def test_preview_creates_real_job_but_does_not_adopt(studio):
    client, sessions, ids, path, tid, url = ready(studio)
    wid = uuid.UUID(path.split("/")[-2])
    document = client.get(url).json()
    document["items"][0]["duration_ms"] = 400
    task = selected(ids, work_id=wid)
    with sessions() as db:
        ctx = context(db, ids)
        proposed = invoke(
            db,
            ctx,
            task,
            "engineering.propose",
            {
                "work_id": str(wid),
                "base_revision": 0,
                "request_key": str(uuid.uuid4()),
                "commands": [
                    {"operation": "timeline.edit", "timeline_id": tid, "document": document}
                ],
            },
        )
        assert proposed.status == "succeeded", proposed.model_dump()
        db.commit()
        args = {
            "work_id": str(wid),
            "change_id": proposed.data["id"],
            "fingerprint": proposed.data["fingerprint"],
        }
        result = invoke(db, ctx, task, "engineering.preview", args)
        assert result.status == "succeeded", result.model_dump()
        job_id = uuid.UUID(result.recovery["job_id"])
        assert db.get(GenerationJob, job_id).status == "succeeded"
        repeated = invoke(db, ctx, task, "engineering.preview", args)
        assert repeated.recovery["job_id"] == str(job_id)
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert client.get(path).json()["revision"] == 0
