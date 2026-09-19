"""Project-wide creative defaults with optimistic concurrency."""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.errors import Conflict
from app.models.identity import Project


class CreativePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0)
    image_count: int = Field(default=4, ge=1, le=4)
    shot_count: int = Field(default=3, ge=1, le=4)
    use_references: bool = True
    generation_guidance: str = Field(default="", max_length=4000)
    assistant_guidance: str = Field(default="", max_length=4000)


def read(project):
    return CreativePreferences(**(project.settings or {}).get("creative_preferences", {}))


def save(db, ctx, data):
    project = db.scalar(
        select(Project)
        .where(Project.id == ctx.project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if read(project).revision != data.revision:
        raise Conflict("创作偏好已被其他人修改，请先重新载入，再合并你的修改")
    result = data.model_copy(update={"revision": data.revision + 1})
    project.settings = {**(project.settings or {}), "creative_preferences": result.model_dump()}
    from app.core import audit

    audit.record(
        db,
        action="project.creative_preferences",
        user_id=ctx.user.id,
        project_id=project.id,
        detail={"revision": result.revision},
    )
    db.flush()
    return result


def guide(db, project_id, prompt, kind):
    text = getattr(read(db.get(Project, project_id)), kind + "_guidance")
    return (
        ("【项目创作偏好】\n" + text + "\n\n【本次要求】\n" + (prompt or ""))
        if text.strip()
        else prompt
    )
