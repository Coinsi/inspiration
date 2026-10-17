"""Explicit adoption copies references, never media bytes or generation charges."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict
from app.models.asset import Asset
from app.models.generation import Generation, GenerationJob
from app.models.identity import Project
from app.models.material import GeneratedMaterialState
from app.models.narrative import Scene, Script
from app.models.shot import Shot
from app.models.work import CreativeWork
from app.modules.generation import jobs


class Adopt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["shot", "asset"]
    target_id: uuid.UUID
    expected_generation_id: uuid.UUID | None


class Selection(BaseModel):
    expected_generation_id: uuid.UUID | None = None


def current(db, target_type, target):
    if target_type == "shot":
        return target.selected_generation_id
    return db.scalar(
        select(Generation.id)
        .where(
            Generation.project_id == target.project_id,
            Generation.target_type == "asset",
            Generation.target_id == target.id,
            Generation.is_selected.is_(True),
            Generation.output_blob_hash == target.representative_blob_hash,
        )
        .order_by(Generation.created_at.desc())
        .limit(1)
    )


def freeze_timeline_sources(db, ctx, shot_id, previous):
    """An adopted replacement must not change clips that already used the old output."""
    if not previous:
        return
    from app.models.timeline import Timeline, TimelineItem
    from app.modules.timeline import editing

    timelines = list(
        db.scalars(
            select(Timeline)
            .join(TimelineItem)
            .where(
                Timeline.project_id == ctx.project.id,
                TimelineItem.shot_id == shot_id,
                TimelineItem.generation_id.is_(None),
            )
            .distinct()
            .order_by(Timeline.id)
        )
    )
    for t in timelines:
        # Includes archived timelines: restoring one must preserve its footage.
        db.refresh(t, with_for_update=True)
        items = list(
            db.scalars(
                select(TimelineItem).where(
                    TimelineItem.timeline_id == t.id,
                    TimelineItem.shot_id == shot_id,
                    TimelineItem.generation_id.is_(None),
                )
            )
        )
        for item in items:
            item.generation_id = previous
        if items:
            t.revision += 1
            db.flush()
            if not t.deleted_at:
                editing.remember(db, t, ctx.user.id, "保留已剪辑画面版本")


def select_output(db, ctx, g, expected=None, check_expected=False):
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    if g.target_type not in ("shot", "asset"):
        raise CapabilityUnsupported("请先选择要采用到的镜头或角色与场景")
    target = jobs.validate_target(db, ctx.project.id, g.target_type, g.target_id)
    db.refresh(target)
    if not g.output_blob_hash or g.output_type not in ("image", "video"):
        raise CapabilityUnsupported("请选择已生成完成的图片或视频")
    if g.target_type == "asset" and g.output_type != "image":
        raise CapabilityUnsupported("角色与场景的代表图只能采用图片")
    job = db.get(GenerationJob, g.job_id)
    if not job or job.status != "succeeded":
        raise CapabilityUnsupported("请等待生成任务成功后再采用")
    previous = current(db, g.target_type, target)
    if previous == g.id:
        return g
    if check_expected and previous != expected:
        raise Conflict("此位置的采用版本已变化，请刷新后确认再采用")
    if g.target_type == "shot":
        freeze_timeline_sources(db, ctx, target.id, previous)
        target.selected_generation_id = g.id
    else:
        target.representative_blob_hash = g.output_blob_hash
    for variant in db.scalars(
        select(Generation).where(
            Generation.project_id == ctx.project.id,
            Generation.target_type == g.target_type,
            Generation.target_id == g.target_id,
        )
    ):
        variant.is_selected = variant.id == g.id
    audit.record(
        db,
        action="generation.select",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type=g.target_type,
        target_id=g.target_id,
        detail={
            "generation": str(g.id),
            "previous_generation": str(previous) if previous else None,
            "source_generation": (g.input_refs or {}).get("adoption_source_id", str(g.id)),
        },
    )
    db.flush()
    return g


def adopt(db, ctx, generation_id, data):
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    source = jobs.source(db, ctx.project.id, generation_id)
    hidden = db.get(GeneratedMaterialState, source.id)
    if hidden and hidden.deleted_at:
        raise Conflict("请先从回收站恢复这份素材")
    target = jobs.validate_target(db, ctx.project.id, data.target_type, data.target_id)
    db.refresh(target)
    if source.target_type == data.target_type and source.target_id == target.id:
        return select_output(db, ctx, source, data.expected_generation_id, True)
    variant = db.scalar(
        select(Generation)
        .where(
            Generation.project_id == ctx.project.id,
            Generation.target_type == data.target_type,
            Generation.target_id == target.id,
            Generation.input_refs["adoption_source_id"].astext == str(source.id),
        )
        .order_by(Generation.created_at)
        .limit(1)
    )
    if not variant:
        variant = Generation(
            project_id=ctx.project.id,
            job_id=source.job_id,
            target_type=data.target_type,
            target_id=target.id,
            provider="library",
            model=source.model,
            output_type=source.output_type,
            output_blob_hash=source.output_blob_hash,
            thumbnail_hash=source.thumbnail_hash,
            prompt_snapshot=source.prompt_snapshot,
            params=source.params,
            cost_points=0,
            input_refs={**(source.input_refs or {}), "adoption_source_id": str(source.id)},
        )
        db.add(variant)
        db.flush()
    return select_output(db, ctx, variant, data.expected_generation_id, True)


def destinations(db, project_id):
    result = []
    asset_choices = dict(
        db.execute(
            select(Asset.id, Generation.id)
            .join(
                Generation, (Generation.target_id == Asset.id) & (Generation.target_type == "asset")
            )
            .where(
                Asset.project_id == project_id,
                Generation.project_id == project_id,
                Generation.is_selected.is_(True),
                Asset.representative_blob_hash == Generation.output_blob_hash,
            )
        ).all()
    )
    for shot, scene, script, work in db.execute(
        select(Shot, Scene, Script, CreativeWork)
        .join(Scene, Shot.scene_id == Scene.id)
        .join(Script, Scene.script_id == Script.id)
        .outerjoin(CreativeWork, CreativeWork.script_id == Script.id)
        .where(
            Shot.project_id == project_id,
            Shot.deleted_at.is_(None),
            Scene.deleted_at.is_(None),
            Script.deleted_at.is_(None),
        )
        .order_by(Script.created_at.desc(), Scene.ordinal, Shot.ordinal)
    ):
        result.append(
            {
                "target_type": "shot",
                "target_id": shot.id,
                "name": shot.title or shot.code,
                "path": f"{work.title if work else script.title} / {scene.title or scene.code}",
                "current_generation_id": shot.selected_generation_id,
                "work_id": work.id if work else None,
            }
        )
    for asset in db.scalars(
        select(Asset)
        .where(Asset.project_id == project_id, Asset.deleted_at.is_(None))
        .order_by(Asset.name)
    ):
        result.append(
            {
                "target_type": "asset",
                "target_id": asset.id,
                "name": asset.name,
                "path": "项目角色与场景",
                "work_id": None,
                "current_generation_id": asset_choices.get(asset.id),
            }
        )
    return result


def usage(db, project_id, generation_id):
    jobs.source(db, project_id, generation_id)
    variants = list(
        db.scalars(
            select(Generation).where(
                Generation.project_id == project_id,
                or_(
                    Generation.id == generation_id,
                    Generation.input_refs["adoption_source_id"].astext == str(generation_id),
                ),
            )
        )
    )
    targets = {(d["target_type"], d["target_id"]): d for d in destinations(db, project_id)}
    uses = []
    for g in variants:
        target = targets.get((g.target_type, g.target_id))
        if target and target["current_generation_id"] == g.id:
            uses.append({**target, "generation_id": g.id})
    return {"state": "adopted" if uses else "candidate", "uses": uses}
