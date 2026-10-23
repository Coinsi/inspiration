"""Versioned kernel and orchestration contracts. State transitions live in service."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.harness.guidance import Decision as GuidanceDecision
from app.modules.skill.schemas import SkillUse
from app.modules.tools.contracts import Target


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Policy(Strict):
    max_parallel: int = Field(default=3, ge=1, le=8)
    max_tasks: int = Field(default=64, ge=1, le=256)
    max_actions: int = Field(default=40, ge=1, le=200)
    max_total_actions: int = Field(default=240, ge=1, le=2000)
    max_active_seconds: int = Field(default=7200, ge=60, le=86400)
    max_preview_jobs: int = Field(default=12, ge=0, le=100)
    max_model_tokens: int = Field(default=4000000, ge=0, le=100000000)
    max_model_output_tokens: int = Field(default=8192, ge=256, le=32768)
    max_output_storage_bytes: int = Field(default=2147483648, ge=0, le=107374182400)
    max_media_transfer_bytes: int = Field(default=4294967296, ge=0, le=214748364800)
    max_depth: int = Field(default=3, ge=0, le=5)
    max_retries: int = Field(default=2, ge=0, le=5)
    effects: list[Literal["read", "candidate", "generate", "preview"]] = Field(
        default_factory=lambda: ["read", "candidate", "preview"]
    )
    budget_points: int = Field(default=0, ge=0, le=1000000)


class TaskSpec(Strict):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    goal: str = Field(min_length=1, max_length=8000)
    role: str = Field(default="worker", min_length=1, max_length=40)
    kind: Literal["agent", "tool"] = "agent"
    dependencies: list[str] = Field(default_factory=list, max_length=64)
    tools: list[str] = Field(default_factory=list, max_length=32)
    tool: str | None = Field(default=None, max_length=120)
    arguments: dict = Field(default_factory=dict)
    expected_output: Literal["reply", "artifact", "candidate", "observation"] = "reply"
    references: list[dict] = Field(default_factory=list, max_length=32)
    output_requirements: dict = Field(default_factory=dict)
    quality_requirements: dict = Field(default_factory=dict)
    scope: list[Target] | None = Field(default=None, max_length=64)
    allowed_tools: list[str] | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def operation(self):
        from app.modules.harness.technical_validation import Expectations

        self.output_requirements = Expectations.model_validate(self.output_requirements).model_dump(
            exclude_none=True
        )
        if self.quality_requirements:
            from app.modules.harness.quality_tools import QualityRequirements

            self.quality_requirements = QualityRequirements.model_validate(
                self.quality_requirements
            ).model_dump(mode="json")
            if (
                self.quality_requirements.get("criteria")
                or self.quality_requirements.get("require_human_review")
            ) and self.expected_output != "artifact":
                raise ValueError("创意和人工验收要求必须对应实际媒体产物交付")
            if self.kind == "tool":
                raise ValueError(
                    "含创意或人工验收要求的步骤需要 Agent 检查，不能以单个工具返回代替"
                )
        if self.kind == "tool" and not self.tool:
            raise ValueError("工具任务必须指定工具")
        if self.kind == "agent" and self.tool:
            raise ValueError("Agent 任务通过结构化动作选择工具")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("任务依赖不能重复")
        return self


class Plan(Strict):
    summary: str = Field(min_length=1, max_length=4000)
    tasks: list[TaskSpec] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def dag(self):
        nodes = {t.key: t for t in self.tasks}
        if len(nodes) != len(self.tasks):
            raise ValueError("任务名称不能重复")
        visited, visiting = set(), set()

        def visit(key):
            if key not in nodes:
                raise ValueError("任务依赖不存在")
            if key in visiting:
                raise ValueError("任务依赖不能循环")
            if key in visited:
                return
            visiting.add(key)
            for dep in nodes[key].dependencies:
                visit(dep)
            visiting.remove(key)
            visited.add(key)

        for key in nodes:
            visit(key)
        return self


class Create(Strict):
    goal: str = Field(min_length=1, max_length=8000)
    request_key: uuid.UUID
    work_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    engine: str = Field(default="cloud_llm", min_length=1, max_length=64)
    model_provider: str | None = Field(default=None, max_length=120)
    scope: list[Target] = Field(default_factory=list, max_length=64)
    references: list[dict] = Field(default_factory=list, max_length=32)
    skills: list[uuid.UUID] = Field(default_factory=list, max_length=8)
    skill_versions: list[SkillUse] = Field(default_factory=list, max_length=8)
    policy: Policy = Field(default_factory=Policy)
    plan: Plan | None = None


class Control(Strict):
    request_key: uuid.UUID
    expected_revision: int = Field(ge=1)
    action: Literal[
        "pause",
        "resume",
        "cancel",
        "retry",
        "steer",
        "approve_plan",
        "reply",
        "approve_tool",
        "reject_tool",
        "update_limits",
        "resume_receipt",
    ]
    task_ids: list[uuid.UUID] = Field(default_factory=list, max_length=256)
    message: str = Field(default="", max_length=8000)
    budget_points: int | None = Field(default=None, ge=0, le=1000000)
    review_id: uuid.UUID | None = None
    limits: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def content(self):
        if self.action == "resume_receipt" and len(self.task_ids) != 1:
            raise ValueError("请选择需要重新接收结果的单个步骤")
        if self.action == "update_limits":
            valid = set(Policy.model_fields) - {"effects", "budget_points"}
            if not self.limits or not set(self.limits) <= valid:
                raise ValueError("请选择有效的制作资源限额")
            Policy.model_validate(self.limits)
        elif self.limits:
            raise ValueError("限额调整需要独立的更新操作")
        if self.action in {"steer", "reply"} and not self.message.strip():
            raise ValueError("请填写指导意见或回复")
        if self.action in {"approve_tool", "reject_tool"} and (
            len(self.task_ids) != 1 or not self.review_id
        ):
            raise ValueError("请选择待审步骤和固定的审阅记录")
        return self


class ToolAction(Strict):
    kind: Literal["tool"]
    tool: str = Field(min_length=1, max_length=120)
    arguments: dict = Field(default_factory=dict)
    message: str = Field(default="", max_length=2000)


class PlanAction(Strict):
    kind: Literal["plan", "delegate"]
    plan: Plan


class QuestionAction(Strict):
    kind: Literal["question"]
    message: str = Field(min_length=1, max_length=4000)


class FinishAction(Strict):
    kind: Literal["finish"]
    message: str = Field(min_length=1, max_length=8000)
    artifacts: list[dict] = Field(default_factory=list, max_length=32)
    quality_report_id: uuid.UUID | None = None


class WaitAction(Strict):
    kind: Literal["wait"]
    change_id: uuid.UUID
    fingerprint: str = Field(min_length=1, max_length=128)
    message: str = Field(default="请审阅工程候选后继续", max_length=2000)


Action = Annotated[
    ToolAction | PlanAction | QuestionAction | FinishAction | WaitAction | GuidanceDecision,
    Field(discriminator="kind"),
]
