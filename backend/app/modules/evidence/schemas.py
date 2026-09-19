import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    aliases: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=20
    )
    description: str = Field(default="", max_length=4000)
    asset_id: uuid.UUID | None = None


class IdentitySave(IdentityIn):
    revision: int = Field(ge=0)


class Correction(Strict):
    identity_id: uuid.UUID | None = None
    kind: Literal["visual", "speech"] = "visual"
    status: Literal["proposed", "confirmed", "rejected"] = "proposed"
    observation: str = Field(min_length=1, max_length=4000)
    note: str = Field(default="", max_length=2000)


class Create(Correction):
    version_id: uuid.UUID
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def range(self):
        if self.end_ms - self.start_ms < 100:
            raise ValueError("证据片段至少0.1秒")
        return self


class Save(Correction):
    revision: int = Field(ge=0)


class Revision(Strict):
    revision: int = Field(ge=0)


class Suggest(Strict):
    query: str = Field(min_length=1, max_length=1000)
    offset: int = Field(default=0, ge=0, le=10000)
