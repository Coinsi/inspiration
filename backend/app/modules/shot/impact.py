"""Follow recorded inputs through generated media, adoption and actual timeline clips."""

from sqlalchemy import func, or_, select

from app.models.generation import Generation
from app.models.shot import Shot, ShotAssetRef
from app.models.timeline import Timeline, TimelineItem
from app.modules.shot.inputs import ASPECTS, look_image, visual_traits


def changed_traits(before, after, look):
    previous, current = visual_traits(before, look), visual_traits(after, look)
    return {k for k in ASPECTS if previous.get(k) != current.get(k)}


def untracked_count(db, project_id, shot_ids, tracked):
    if not shot_ids:
        return 0
    return db.scalar(
        select(func.count(Generation.id)).where(
            Generation.project_id == project_id,
            Generation.target_type == "shot",
            Generation.target_id.in_(shot_ids),
            Generation.id.not_in([row["id"] for row in tracked]),
            Generation.input_refs["shot_dependencies"].is_(None),
        )
    )


def scene_impact(db, project_id, scene_id, change=None):
    from app.core.errors import Conflict, NotFound
    from app.models.narrative import Scene

    stmt = select(Scene).where(
        Scene.project_id == project_id, Scene.id == scene_id, Scene.deleted_at.is_(None)
    )
    scene = db.scalar(stmt.with_for_update() if change else stmt)
    if not scene:
        raise NotFound("场景不存在")
    if change:
        if scene.visual_brief != change.previous:
            raise Conflict("其他人已更新场景设定，请刷新后再保存")
        scene.visual_brief = change.brief.strip()
    shots = list(
        db.scalars(
            select(Shot)
            .where(Shot.scene_id == scene.id, Shot.deleted_at.is_(None))
            .order_by(Shot.ordinal)
        )
    )
    roots = list(
        db.scalars(
            select(Generation).where(
                Generation.project_id == project_id,
                Generation.input_refs["scene_visual_source"].astext == str(scene.id),
            )
        )
    )
    outputs, clips = trace_outputs(db, project_id, roots)
    inherited = [
        {"id": s.id, "title": s.title or s.code}
        for s in shots
        if not s.prompt_override and (s.storyboard or {}).get("inherit_scene", True)
    ]
    return {
        "brief": scene.visual_brief,
        "shots": inherited,
        "outputs": outputs,
        "clips": clips,
        "untracked_outputs": untracked_count(db, project_id, [s.id for s in shots], outputs),
    }


def trace_outputs(db, project_id, roots):
    outputs = {g.id: g for g in roots}
    frontier = list(outputs.values())
    while frontier:
        ids = [str(g.id) for g in frontier]
        keys = (
            "source_generation_id",
            "first_frame_id",
            "last_frame_id",
            "edit_source_id",
            "adoption_source_id",
        )
        refs = Generation.input_refs
        children = db.scalars(
            select(Generation).where(
                Generation.project_id == project_id, or_(*(refs[k].astext.in_(ids) for k in keys))
            )
        )
        frontier = [g for g in children if g.id not in outputs]
        outputs.update((g.id, g) for g in frontier)
    if not outputs:
        return [], []
    selected = set(
        db.scalars(
            select(Shot.selected_generation_id).where(
                Shot.project_id == project_id,
                Shot.deleted_at.is_(None),
                Shot.selected_generation_id.in_(outputs),
            )
        )
    )
    clips = db.execute(
        select(TimelineItem, Timeline.name)
        .join(Timeline, Timeline.id == TimelineItem.timeline_id)
        .outerjoin(Shot, Shot.id == TimelineItem.shot_id)
        .where(
            Timeline.project_id == project_id,
            Timeline.deleted_at.is_(None),
            func.coalesce(TimelineItem.generation_id, Shot.selected_generation_id).in_(outputs),
        )
        .order_by(Timeline.name, TimelineItem.ordinal)
    )
    direct = {r.id for r in roots}
    return (
        [
            {
                "id": g.id,
                "type": g.output_type,
                "target_id": g.target_id,
                "target_type": g.target_type,
                "blob_hash": g.output_blob_hash,
                "created_at": g.created_at,
                "adopted": g.id in selected,
                "direct": g.id in direct,
            }
            for g in outputs.values()
        ],
        [
            {
                "id": item.id,
                "timeline_id": item.timeline_id,
                "timeline": title,
                "ordinal": item.ordinal,
            }
            for item, title in clips
        ],
    )


def preview(db, asset, change):
    before = asset.meta or {}
    after = change.metadata if change and change.metadata is not None else before
    renamed = change is not None and change.name is not None and change.name != asset.name
    summary_changed = (
        change is not None and change.summary is not None and change.summary != asset.summary
    )
    general = change is None or renamed or summary_changed

    def reason(usage):
        look = usage.get("look", "")
        if change is not None and "prompt_parts" in usage and not usage["prompt_parts"]:
            if (
                usage.get("reference_hashes")
                and look
                and look_image(before, look) != look_image(after, look)
            ):
                return "造型参考图"
            return ""
        uses_description = usage.get(
            "uses_description",
            not look and not (usage.get("aspects") and visual_traits(before, look)),
        )
        if change is None or renamed or (summary_changed and uses_description):
            return "使用了这项资产"
        if look and look_image(before, look) != look_image(after, look):
            return "造型参考图"
        aspects = changed_traits(before, after, usage.get("look", ""))
        if usage.get("aspects"):
            aspects.intersection_update(usage["aspects"])
        return "、".join(ASPECTS[a] for a in ASPECTS if a in aspects)

    rows = db.execute(
        select(Shot, ShotAssetRef)
        .join(ShotAssetRef, Shot.id == ShotAssetRef.shot_id)
        .where(
            Shot.project_id == asset.project_id,
            Shot.deleted_at.is_(None),
            ShotAssetRef.asset_id == asset.id,
        )
        .order_by(Shot.code)
    )
    shots = []
    bound = set()
    for shot, ref in rows:
        bound.add(shot.id)
        usage = ref.usage or {}
        why = reason(usage)
        if why:
            shots.append(
                {
                    "id": shot.id,
                    "title": shot.title or shot.code,
                    "scene_id": shot.scene_id,
                    "state": "pinned" if ref.ref_mode == "pinned" else "affected",
                    "reason": why,
                    "evidence": usage.get("evidence", ""),
                    "look": usage.get("look", ""),
                }
            )
    aliases = before.get("aliases")
    names = (
        [asset.name, *(n for n in aliases if isinstance(n, str) and n)]
        if isinstance(aliases, list)
        else [asset.name]
    )
    looks = {""}
    for meta in (before, after):
        if isinstance(meta.get("looks"), dict):
            looks.update(meta["looks"])
    if general or any(
        changed_traits(before, after, k) or look_image(before, k) != look_image(after, k)
        for k in looks
    ):
        mentions = db.scalars(
            select(Shot)
            .where(
                Shot.project_id == asset.project_id,
                Shot.deleted_at.is_(None),
                Shot.id.not_in(bound),
                or_(
                    *(
                        column.contains(name, autoescape=True)
                        for name in names
                        for column in (Shot.title, Shot.description)
                    ),
                    *(
                        Shot.storyboard["unresolved_subjects"].contains([{"name": name}])
                        for name in names
                    ),
                ),
            )
            .order_by(Shot.code)
        )
        shots.extend(
            {
                "id": s.id,
                "title": s.title or s.code,
                "scene_id": s.scene_id,
                "state": "possible",
                "reason": "文字提及，尚未确认是否入镜",
                "evidence": "",
                "look": "",
            }
            for s in mentions
        )
    roots = []
    for g in db.scalars(
        select(Generation).where(
            Generation.project_id == asset.project_id,
            Generation.input_refs["shot_dependencies"].contains([{"asset_id": str(asset.id)}]),
        )
    ):
        for d in g.input_refs["shot_dependencies"]:
            if d["asset_id"] == str(asset.id) and d.get("ref_mode") != "pinned" and reason(d):
                roots.append(g)
                break
    outputs, clips = trace_outputs(db, asset.project_id, roots)
    return {
        "shots": shots,
        "outputs": outputs,
        "clips": clips,
        "untracked_outputs": untracked_count(
            db, asset.project_id, [s["id"] for s in shots if s["state"] != "pinned"], outputs
        ),
    }
