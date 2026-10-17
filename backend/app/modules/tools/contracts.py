import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["script", "scene", "shot", "asset", "setting"]
    target_id: uuid.UUID


@dataclass
class ToolContext:
    """Capabilities explicitly selected by the caller, not an agent session."""

    scope: list[dict] = field(default_factory=list)
    skill_snapshot: list[dict] = field(default_factory=list)
