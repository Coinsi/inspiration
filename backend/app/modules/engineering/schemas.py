import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.engineering.native import Time
from app.modules.timeline.schemas import TimelineDocumentIn


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Add(Strict):
    operation: Literal["resource.add"]
    usage_id: uuid.UUID
    source_kind: Literal["library", "generation"]
    source_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def nonblank(cls, v):
        if not v.strip():
            raise ValueError("请填写名称")
        return v.strip()


class Remove(Strict):
    operation: Literal["resource.remove"]
    usage_id: uuid.UUID


class Restore(Strict):
    operation: Literal["restore"]
    revision: int = Field(ge=1)


class CaptureTimeline(Strict):
    operation: Literal["timeline.capture"]
    timeline_id: uuid.UUID
    timeline_revision: int = Field(ge=0)


class EditTimeline(Strict):
    operation: Literal["timeline.edit"]
    timeline_id: uuid.UUID
    document: TimelineDocumentIn
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"


class RestoreTimeline(Strict):
    operation: Literal["timeline.restore"]
    timeline_id: uuid.UUID
    timeline_revision: int = Field(ge=0)
    engineering_revision: int = Field(ge=1)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"


class EditDocument(Strict):
    operation: Literal["document.edit"]
    document_id: uuid.UUID
    document_revision: int = Field(ge=1)
    timeline_revision: int = Field(ge=0)
    document: TimelineDocumentIn | None = None
    aspect_ratio: Literal["16:9", "9:16", "1:1"] | None = None


class PutNativeDocument(Strict):
    operation: Literal["document.put"]
    document_id: uuid.UUID
    expected_revision: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=40)
    schema_version: int = Field(default=1, ge=1, le=1000)
    title: str = Field(min_length=1, max_length=255)
    inputs: dict = Field(default_factory=dict)
    payload: dict
    publish_output: bool = True


class ImportPackage(Strict):
    operation: Literal["package.import"]
    package: dict


class MigrateDocument(Strict):
    operation: Literal["document.migrate"]
    document_id: uuid.UUID
    document_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    target_schema_version: int = Field(ge=1, le=1000)


class FixedReference(Strict):
    id: uuid.UUID
    revision: int = Field(ge=1)


class CandidateReference(Strict):
    candidate_document: uuid.UUID


class RetargetInput(Strict):
    alias: str = Field(min_length=1, max_length=255)
    expected: FixedReference
    target: FixedReference | CandidateReference


class RetargetDocument(Strict):
    operation: Literal["document.retarget"]
    document_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    inputs: list[RetargetInput] = Field(min_length=1, max_length=100)


class ReplaceInstanceInput(Strict):
    instance_id: uuid.UUID
    slot: Literal["source", "mask"] = "source"
    expected: FixedReference
    target: FixedReference | CandidateReference


class RetargetInstances(Strict):
    operation: Literal["instance.retarget"]
    document_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    instances: list[ReplaceInstanceInput] = Field(min_length=1, max_length=100)


class InstancePathStep(Strict):
    instance_id: uuid.UUID
    slot: Literal["source", "mask"] = "source"
    expected: FixedReference


class RetargetInstancePath(Strict):
    operation: Literal["instance.retarget_path"]
    document_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    path: list[InstancePathStep] = Field(min_length=1, max_length=16)
    target: FixedReference | CandidateReference


Command = Annotated[
    Add
    | Remove
    | Restore
    | CaptureTimeline
    | EditTimeline
    | RestoreTimeline
    | EditDocument
    | PutNativeDocument
    | ImportPackage
    | MigrateDocument
    | RetargetDocument
    | RetargetInstances
    | RetargetInstancePath,
    Field(discriminator="operation"),
]


class ReadPrecondition(Strict):
    kind: Literal["work", "document", "resource_usage", "timeline"]
    id: uuid.UUID
    revision: int | None = Field(default=None, ge=0)


class Provenance(Strict):
    # Attribution only: never grants permissions or proves a task owns this work.
    run_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    attempt_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None


class Propose(Strict):
    request_key: uuid.UUID
    base_revision: int = Field(ge=0)
    commands: list[Command] = Field(min_length=1, max_length=100)
    read_preconditions: list[ReadPrecondition] = Field(default_factory=list, max_length=2000)
    provenance: Provenance = Field(default_factory=Provenance)


class Rebase(Strict):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_key: uuid.UUID
    expected_revision: int = Field(ge=0)


class Accept(Strict):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledged_reviews: list[str] = Field(default_factory=list, max_length=2000)


class Preview(Strict):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    retry_job_id: uuid.UUID | None = None
    output_id: str | None = None


class LocateTime(Strict):
    document_id: uuid.UUID
    revision: int = Field(ge=1)
    instance_id: uuid.UUID
    direction: Literal["to_source", "to_local"]
    time: Time
