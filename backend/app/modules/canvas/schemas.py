import json
import math
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.generation.schemas import GenerateIn


class Position(BaseModel):
    x: float = Field(ge=-100000, le=100000)
    y: float = Field(ge=-100000, le=100000)

    @field_validator("x", "y")
    @classmethod
    def finite(cls, v):
        if not math.isfinite(v):
            raise ValueError("坐标必须是有限数值")
        return v


class CanvasGenerate(GenerateIn):
    provider: str = Field(default="mock", max_length=120)
    prompt_override: str | None = Field(default=None, max_length=10000)

    @field_validator("params", "provider_params")
    @classmethod
    def bounded_params(cls, v):
        if len(json.dumps(v, allow_nan=False)) > 32000:
            raise ValueError("生成参数过大")
        return v


class Mention(BaseModel):
    node_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    alias: str = Field(min_length=1, max_length=120)


class NodeData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "asset", "shot", "generate", "group"]
    label: str = Field(default="未命名", max_length=120)
    text: str = Field(default="", max_length=10000)
    target_type: Literal["asset", "shot"] = "shot"
    target_id: uuid.UUID | None = None
    generation_id: uuid.UUID | None = None
    generate: CanvasGenerate = Field(default_factory=CanvasGenerate)
    mentions: list[Mention] = Field(default_factory=list, max_length=50)


class Node(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    position: Position
    data: NodeData
    parentId: str | None = None
    width: float = Field(default=260, ge=180, le=4000)
    height: float = Field(default=210, ge=120, le=4000)


class Edge(BaseModel):
    id: str = Field(max_length=100, min_length=1)
    source: str
    target: str
    sourceHandle: Literal["text", "image"] = "text"
    targetHandle: Literal["prompt", "reference"] = "prompt"


class Document(BaseModel):
    nodes: list[Node] = Field(default_factory=list, max_length=500)
    edges: list[Edge] = Field(default_factory=list, max_length=1500)
    viewport: dict = Field(default_factory=lambda: {"x": 0, "y": 0, "zoom": 1})

    @field_validator("viewport")
    @classmethod
    def viewport_valid(cls, v):
        if set(v) != {"x", "y", "zoom"} or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
            for x in v.values()
        ):
            raise ValueError("视口无效")
        if abs(v["x"]) > 1000000 or abs(v["y"]) > 1000000 or not 0.05 <= v["zoom"] <= 4:
            raise ValueError("视口超出范围")
        return v


class Create(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class Save(Create):
    revision: int = Field(ge=0)
    document: Document


class RevisionIn(BaseModel):
    revision: int = Field(ge=0)


class StartRun(RevisionIn):
    reuse_unchanged: bool = False
    request_key: uuid.UUID
    target_node_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class Control(BaseModel):
    action: Literal["pause", "resume", "cancel", "retry"]
