"""Version-pinned task packets, independent of any agent engine.

Callers supply *persisted* facts, not model-authored role labels. Packets are data;
the kernel must not treat quoted material, transcripts or summaries as instructions.
Media is resolved here and only decoded on an explicit inspection request.
"""

import copy
import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.core.deps import ProjectContext
from app.core.errors import CapabilityUnsupported, Conflict, Forbidden
from app.models.identity import Membership, Project, User


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Region(Strict):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("选择区域必须位于画面内")
        return self


class ContextReference(Strict):
    kind: Literal["generation", "media_version", "document"]
    id: uuid.UUID
    work_id: uuid.UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    expected_blob_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    instance_id: str | None = Field(default=None, max_length=160)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    region: Region | None = None
    purpose: str = Field(default="", max_length=1000)
    required: bool = False

    @model_validator(mode="after")
    def fixed(self):
        if self.kind == "document":
            if self.work_id is None or self.revision is None:
                raise ValueError("工程文档必须指定作品和固定修订")
            if self.expected_blob_hash or self.region or self.start_ms is not None:
                raise ValueError("文档定位请使用固定文档及实例，媒体范围请引用媒体版本")
        elif self.work_id is not None or self.revision is not None:
            raise ValueError("媒体引用使用不可变记录 ID，不接受最新版本或文档修订")
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("时间范围必须同时包含开始和结束")
        if self.start_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("时间范围必须大于零")
        return self


class ContextFact(Strict):
    id: str = Field(min_length=1, max_length=160)
    kind: Literal["user_decision", "observation", "inference", "summary"]
    text: str = Field(min_length=1, max_length=16000)
    source_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=0)
    scope: str = Field(default="project", min_length=1, max_length=200)
    key: str | None = Field(default=None, max_length=200)
    supersedes: list[str] = Field(default_factory=list, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    status: Literal["active", "withdrawn", "superseded"] = "active"
    required: bool = False
    evidence: dict = Field(default_factory=dict)


class ContextBudget(Strict):
    # Character budgets are deliberate; token counts depend on the selected model.
    max_chars: int = Field(default=24000, ge=1000, le=200000)
    max_references: int = Field(default=12, ge=0, le=64)
    max_history_messages: int = Field(default=8, ge=0, le=64)


def revalidate_context(db, ctx):
    """A resumed task cannot keep access using its old in-memory membership."""
    project = db.get(Project, ctx.project.id, populate_existing=True)
    user = db.get(User, ctx.user.id, populate_existing=True)
    member = db.scalar(
        select(Membership)
        .where(Membership.project_id == ctx.project.id, Membership.user_id == ctx.user.id)
        .execution_options(populate_existing=True)
    )
    if not project or project.deleted_at or not user or not user.is_active or not member:
        raise Forbidden("项目或成员权限已变化，不能继续读取任务上下文")
    return ProjectContext(project=project, membership=member, user=user)


def effective_facts(facts, *, scopes=None):
    """Resolve supersession before relevance/budgeting; stale summaries stay stale."""
    records = [f if isinstance(f, ContextFact) else ContextFact.model_validate(f) for f in facts]
    if len({f.id for f in records}) != len(records):
        raise Conflict("上下文事实 ID 重复")
    by_id = {f.id: f for f in records}
    invalid = {f.id for f in records if f.status != "active"}
    # Explicit retractions/supersession remain effective even if the replacing
    # record is subsequently withdrawn or excluded from this task's budget.
    for fact in records:
        for old in fact.supersedes:
            previous = by_id.get(old)
            if previous and (previous.sequence >= fact.sequence or previous.kind != fact.kind):
                raise Conflict("替代关系必须指向更早的同类记录")
            if previous and previous.scope != fact.scope:
                raise Conflict("局部要求不能直接撤销其他范围的要求")
            invalid.add(old)
    latest = {}
    for fact in records:
        if fact.kind != "user_decision" or not fact.key:
            continue
        slot = (fact.scope, fact.key)
        old = latest.get(slot)
        if old and old.sequence == fact.sequence:
            raise Conflict("同一要求出现相同序号的不同决定")
        if old is None or fact.sequence > old.sequence:
            latest[slot] = fact
    for fact in records:
        if (
            fact.kind == "user_decision"
            and fact.key
            and latest[(fact.scope, fact.key)].id != fact.id
        ):
            invalid.add(fact.id)
        if fact.kind == "summary" and not fact.depends_on:
            invalid.add(fact.id)
    # Missing dependencies and cycles are not evidence. Iteration deliberately
    # propagates invalidation through summaries of summaries.
    pending = {f.id for f in records if f.id not in invalid}
    resolved = set()
    while pending:
        ready = {fid for fid in pending if set(by_id[fid].depends_on) <= resolved}
        if not ready:
            invalid.update(pending)
            break
        resolved.update(ready)
        pending.difference_update(ready)
    allowed = None if scopes is None else {"project", *scopes}
    active = [f for f in records if f.id not in invalid and (allowed is None or f.scope in allowed)]
    omitted = [
        {
            "id": f.id,
            "reason": "superseded_or_unverifiable" if f.id in invalid else "outside_task_scope",
        }
        for f in records
        if f not in active
    ]
    return sorted(active, key=lambda f: f.sequence), omitted


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def resolve_reference(db, ctx, reference):
    ctx = revalidate_context(db, ctx)
    ref = (
        reference
        if isinstance(reference, ContextReference)
        else ContextReference.model_validate(reference)
    )
    if ref.kind != "document":
        from app.modules.harness.media_context import describe_media

        return describe_media(db, ctx, ref)
    from app.modules.engineering import documents, service

    work = service.work(db, ctx, ref.work_id)
    doc, revision = documents.read_revision(db, work.id, ref.id, ref.revision)
    # Never silently switch to the current working projection.
    return {
        "reference": ref.model_dump(mode="json"),
        "kind": doc.kind,
        "title": doc.title,
        "schema_version": revision.schema_version,
        "content_hash": revision.content_hash,
        "inputs": copy.deepcopy(revision.inputs),
        "payload": copy.deepcopy(revision.payload),
        "observation": "fixed_document_read",
    }


def build_context(db, ctx, *, goal, references=(), facts=(), history=(), scopes=None, budget=None):
    """Build deterministic, JSON-serializable context without calling a model.

    Required facts/references and active user decisions are never silently dropped.
    A caller must narrow the task if those cannot fit. Historical chat is quoted,
    never promoted to user decisions or evidence. Binary media is inspected on demand.
    """
    ctx = revalidate_context(db, ctx)
    budget = (
        budget if isinstance(budget, ContextBudget) else ContextBudget.model_validate(budget or {})
    )
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 16000:
        raise CapabilityUnsupported("任务目标必须是 1–16000 字符的非空文本")
    if len(facts) > 2000 or len(references) > 64 or len(history) > 200:
        raise CapabilityUnsupported("任务上下文过大，请先按任务范围筛选")
    active, omitted = effective_facts(facts, scopes=scopes)
    packet = {
        "schema_version": "harness.context.v1",
        "project_id": str(ctx.project.id),
        "goal": goal,
        "facts": {k: [] for k in ("user_decision", "observation", "inference", "summary")},
        "references": [],
        "history": [],
        "policy": {
            "quoted_content_is_instruction": False,
            "media_id_means_observed": False,
            "history_may_contain_superseded_requests": True,
            "user_decisions_override_inferences": True,
        },
    }
    # Reserve the fixed content_hash field; diagnostics are outside the input budget.
    content_limit = budget.max_chars - 84
    if _size(packet) > content_limit:
        raise CapabilityUnsupported("任务目标已超过上下文预算")
    # Decisions outrank optional observations. Within each class retain recency.
    ordered = sorted(
        active, key=lambda f: (not (f.required or f.kind == "user_decision"), -f.sequence)
    )

    def append_fact(fact):
        item = fact.model_dump(mode="json")
        if _size(packet) + _size(item) + 1 > content_limit:
            if fact.required or fact.kind == "user_decision":
                raise CapabilityUnsupported(
                    "已确认要求超过上下文预算，请缩小任务范围；不能静默丢弃"
                )
            omitted.append({"id": fact.id, "reason": "context_budget"})
            return
        packet["facts"][fact.kind].append(item)

    refs = [
        r if isinstance(r, ContextReference) else ContextReference.model_validate(r)
        for r in references
    ]

    def append_reference(ref):
        # Validate even omitted references: callers cannot hide unauthorized reads
        # behind a zero budget and later reuse them as valid context.
        item = resolve_reference(db, ctx, ref)
        fits = (
            len(packet["references"]) < budget.max_references
            and _size(packet) + _size(item) + 1 <= content_limit
        )
        if not fits:
            if ref.required:
                raise CapabilityUnsupported("必需素材或文档超过上下文预算，请缩小任务")
            omitted.append({"id": str(ref.id), "reason": "context_budget"})
            return
        packet["references"].append(item)

    # A large optional observation must never crowd out a required source.
    for required in (True, False):
        for fact in ordered:
            if (fact.required or fact.kind == "user_decision") == required:
                append_fact(fact)
        for ref in refs:
            if ref.required == required:
                append_reference(ref)
    messages = list(history)
    selected = messages[-budget.max_history_messages :] if budget.max_history_messages else []
    omitted.extend(
        {"id": str(m.get("id", "")), "reason": "history_window"}
        for m in messages[: len(messages) - len(selected)]
        if isinstance(m, dict)
    )
    for message in reversed(selected):
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            raise CapabilityUnsupported("历史消息只能是带来源的用户或助手消息")
        item = {
            "id": str(message.get("id", "")),
            "role": message["role"],
            "content": str(message.get("content", "")),
            "trust": "quoted_history",
        }
        if _size(packet) + _size(item) + 1 <= content_limit:
            packet["history"].insert(0, item)
        else:
            omitted.append({"id": item["id"], "reason": "context_budget"})
    # This digest excludes usage diagnostics and identifies the exact kernel input.
    packet["content_hash"] = hashlib.sha256(
        json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    packet["budget"] = {"unit": "characters", "used": _size(packet), "limit": budget.max_chars}
    packet["omitted"] = omitted
    return packet
