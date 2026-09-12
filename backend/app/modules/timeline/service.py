"""timeline 业务:时间线组装、成片(cut)、定剪冻结基线、基线对比。"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.kernel.versioning import VersioningService
from app.models.enums import CutKind, VersionStatus
from app.models.shot import Shot
from app.models.timeline import Cut, Timeline, TimelineItem
from app.models.versioning import Baseline, BaselineItem, Version
from app.modules.timeline import schemas
from app.platform import coding


def create_timeline(db: Session, ctx: ProjectContext, data: schemas.TimelineIn) -> Timeline:
    t = Timeline(project_id=ctx.project.id, name=data.name, kind=data.kind, created_by=ctx.user.id)
    db.add(t)
    db.flush()
    return t


def list_timelines(db: Session, project_id: uuid.UUID) -> list[Timeline]:
    return list(
        db.scalars(
            select(Timeline)
            .where(Timeline.project_id == project_id, Timeline.deleted_at.is_(None))
            .order_by(Timeline.created_at.desc())
        )
    )


def _get_timeline(db: Session, project_id: uuid.UUID, timeline_id: uuid.UUID) -> Timeline:
    t = db.get(Timeline, timeline_id)
    if t is None or t.project_id != project_id or t.deleted_at is not None:
        raise NotFound("时间线不存在")
    return t


def set_items(
    db: Session, ctx: ProjectContext, timeline_id: uuid.UUID, items
) -> list[TimelineItem]:
    t = _get_timeline(db, ctx.project.id, timeline_id)
    from app.core.errors import CapabilityUnsupported
    from app.modules.generation.jobs import source, validate_target

    for it in items:
        validate_target(db, ctx.project.id, "shot", it.shot_id)
        if it.generation_id:
            g = source(db, ctx.project.id, it.generation_id)
            if g.target_type != "shot" or g.target_id != it.shot_id:
                raise CapabilityUnsupported("素材不属于该镜头")
        if it.out_point_ms and it.out_point_ms <= it.in_point_ms:
            raise CapabilityUnsupported("出点必须晚于入点")
    db.query(TimelineItem).filter(TimelineItem.timeline_id == t.id).delete()
    created = []
    for i, it in enumerate(items):
        ti = TimelineItem(
            timeline_id=t.id,
            shot_id=it.shot_id,
            generation_id=it.generation_id,
            ordinal=i,
            in_point_ms=it.in_point_ms,
            out_point_ms=it.out_point_ms,
            duration_ms=it.duration_ms,
            transition=it.transition,
            note=it.note,
        )
        db.add(ti)
        created.append(ti)
    db.flush()
    return created


def list_items(db: Session, project_id: uuid.UUID, timeline_id: uuid.UUID) -> list[TimelineItem]:
    _get_timeline(db, project_id, timeline_id)
    return list(
        db.scalars(
            select(TimelineItem)
            .where(TimelineItem.timeline_id == timeline_id)
            .order_by(TimelineItem.ordinal)
        )
    )


def create_cut(db: Session, ctx: ProjectContext, data: schemas.CutIn) -> Cut:
    _get_timeline(db, ctx.project.id, data.timeline_id)
    cut = Cut(
        project_id=ctx.project.id,
        code=coding.next_code(db, ctx.project, "cut"),
        name=data.name,
        kind=data.kind,
        timeline_id=data.timeline_id,
        created_by=ctx.user.id,
    )
    db.add(cut)
    db.flush()
    return cut


def _get_cut(db: Session, project_id: uuid.UUID, cut_id: uuid.UUID) -> Cut:
    c = db.get(Cut, cut_id)
    if c is None or c.project_id != project_id:
        raise NotFound("成片不存在")
    return c


def finalize_cut(db: Session, ctx: ProjectContext, cut_id: uuid.UUID) -> Baseline:
    """定剪:冻结时间线内所有镜头版本为一个基线,锁定成片。"""
    cut = _get_cut(db, ctx.project.id, cut_id)
    db.refresh(cut, with_for_update=True)
    if cut.status == VersionStatus.locked:
        raise Conflict("该成片已经定剪")
    items = list_items(db, ctx.project.id, cut.timeline_id)
    if not items:
        raise CapabilityUnsupported("时间线为空，不能定剪")
    baseline = Baseline(
        project_id=ctx.project.id,
        name=f"{cut.name}-{cut.kind.value if isinstance(cut.kind, CutKind) else cut.kind}",
        kind=cut.kind.value if isinstance(cut.kind, CutKind) else cut.kind,
        note=f"由成片 {cut.code} 定剪冻结",
        created_by=ctx.user.id,
    )
    db.add(baseline)
    db.flush()

    vs = VersioningService(db)
    seen = set()
    for it in items:
        if it.shot_id in seen:
            continue
        seen.add(it.shot_id)
        shot = db.get(Shot, it.shot_id)
        if shot is None:
            continue
        snapshot = {
            "code": shot.code,
            "title": shot.title,
            "description": shot.description,
            "production_status": shot.production_status
            if isinstance(shot.production_status, str)
            else shot.production_status.value,
            "selected_generation_id": str(shot.selected_generation_id)
            if shot.selected_generation_id
            else None,
            "ordinal": it.ordinal,
            "generation_id": str(it.generation_id or shot.selected_generation_id)
            if (it.generation_id or shot.selected_generation_id)
            else None,
            "in_point_ms": it.in_point_ms,
            "out_point_ms": it.out_point_ms,
            "duration_ms": it.duration_ms,
            "timeline_items": [
                {
                    "ordinal": clip.ordinal,
                    "generation_id": str(clip.generation_id or shot.selected_generation_id)
                    if (clip.generation_id or shot.selected_generation_id)
                    else None,
                    "in_point_ms": clip.in_point_ms,
                    "out_point_ms": clip.out_point_ms,
                    "duration_ms": clip.duration_ms,
                    "transition": clip.transition,
                }
                for clip in items
                if clip.shot_id == shot.id
            ],
        }
        v = vs.commit(
            project_id=ctx.project.id,
            entity_type="shot",
            entity_id=shot.id,
            content=snapshot,
            created_by=ctx.user.id,
            label=f"基线 {baseline.name}",
            status=VersionStatus.locked,
        )
        v.is_locked = True
        shot.current_version_id = v.id
        db.add(
            BaselineItem(
                baseline_id=baseline.id, entity_type="shot", entity_id=shot.id, version_id=v.id
            )
        )

    cut.baseline_id = baseline.id
    cut.status = VersionStatus.locked
    audit.record(
        db,
        action="cut.finalize",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="cut",
        target_id=cut.id,
        detail={"baseline": str(baseline.id), "shots": len(items)},
    )
    db.flush()
    return baseline


def list_baselines(db: Session, project_id: uuid.UUID) -> list[Baseline]:
    return list(
        db.scalars(
            select(Baseline)
            .where(Baseline.project_id == project_id)
            .order_by(Baseline.created_at.desc())
        )
    )


def compare_baselines(db: Session, project_id: uuid.UUID, a_id: uuid.UUID, b_id: uuid.UUID) -> dict:
    for bid in (a_id, b_id):
        baseline = db.get(Baseline, bid)
        if baseline is None or baseline.project_id != project_id:
            raise NotFound("基线不存在")

    def items_map(bid: uuid.UUID) -> dict[str, uuid.UUID]:
        rows = db.scalars(select(BaselineItem).where(BaselineItem.baseline_id == bid))
        return {f"{r.entity_type}:{r.entity_id}": r.version_id for r in rows}

    a, b = items_map(a_id), items_map(b_id)
    keys = set(a) | set(b)
    changed, added, removed = [], [], []
    for k in keys:
        if k not in a:
            added.append(k)
        elif k not in b:
            removed.append(k)
        elif a[k] != b[k]:
            va = db.get(Version, a[k])
            vb = db.get(Version, b[k])
            changed.append(
                {
                    "entity": k,
                    "from_version": va.version_no if va else None,
                    "to_version": vb.version_no if vb else None,
                }
            )
    return {"changed": changed, "added": added, "removed": removed}
