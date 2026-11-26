"""Keep checked screenplay evidence separate from editable prompts and finished media."""

import hashlib
import json

from sqlalchemy import select

from app.core.errors import Conflict, NotFound
from app.models.narrative import Scene, Script
from app.models.shot import Shot, ShotScriptSource


def passages(blocks):
    if not blocks:
        return {}
    texts = {}
    speaker = ""
    for block in blocks:
        kind, text = block["block_type"], block["text"]
        if kind == "character":
            speaker = text
        elif kind not in {"parenthetical", "dialogue"}:
            speaker = ""
        if kind == "dialogue" and speaker:
            text = f"{speaker}：{text}"
        texts[block["id"]] = text
    texts[""] = "\n".join(texts.values())
    return texts


def matching_blocks(blocks, quotes):
    result = set()
    for quote in set(quotes):
        needle = "".join(quote.split())
        if len(needle) < 2:
            continue
        matches = [b["id"] for b in blocks if needle in "".join(b["text"].split())]
        if len(matches) == 1:
            result.add(matches[0])
    return result


def previous_scene(db, scene):
    if not scene.script_position or scene.script_position <= 1:
        return None
    return db.scalar(
        select(Scene).where(
            Scene.script_id == scene.script_id,
            Scene.script_position == scene.script_position - 1,
            Scene.deleted_at.is_(None),
        )
    )


def attach(db, shot, scene, suggestion=None):
    if scene.script_position is None or not scene.script_blocks:
        return
    if db.scalar(select(ShotScriptSource.id).where(ShotScriptSource.shot_id == shot.id).limit(1)):
        return
    quotes = suggestion.source_quotes if suggestion else []
    keys = matching_blocks(scene.script_blocks, quotes)
    if not keys and suggestion:
        # Legacy drafts can still provide a verbatim action or a line of dialogue.
        keys = matching_blocks(scene.script_blocks, [suggestion.description, suggestion.dialogue])
    if keys and scene.script_blocks[0]["block_type"] == "scene_heading":
        keys.add(scene.script_blocks[0]["id"])
    origins = [(scene, keys or {""})]
    if suggestion and suggestion.continuity_quotes:
        previous = previous_scene(db, scene)
        if previous:
            origins.append(
                (previous, matching_blocks(previous.script_blocks, suggestion.continuity_quotes))
            )
    for origin, block_ids in origins:
        texts = passages(origin.script_blocks)
        for key in block_ids:
            db.add(
                ShotScriptSource(
                    shot_id=shot.id,
                    scene_id=origin.id,
                    block_id=key,
                    accepted_text=texts[key],
                    current_text=texts[key],
                )
            )


def update_scene(db, scene, before, after):
    old, current = passages(before), passages(after)
    if old and old != current:
        # Old/manual shots without evidence get an explicitly scene-level comparison.
        earlier = (
            select(ShotScriptSource.id)
            .where(
                ShotScriptSource.shot_id == Shot.id,
                ShotScriptSource.scene_id == scene.id,
            )
            .exists()
        )
        for shot_id in db.scalars(select(Shot.id).where(Shot.scene_id == scene.id, ~earlier)):
            db.add(
                ShotScriptSource(
                    shot_id=shot_id,
                    scene_id=scene.id,
                    block_id="",
                    accepted_text=old[""],
                    current_text=current.get(""),
                )
            )
    for row in db.scalars(
        select(ShotScriptSource)
        .join(Shot, Shot.id == ShotScriptSource.shot_id)
        .where(ShotScriptSource.scene_id == scene.id, Shot.scene_id == scene.id)
    ):
        row.current_text = current.get(row.block_id)
    refresh_continuity(db, scene.script_id, scene.id)


def refresh_continuity(db, script_id, origin_id=None):
    """A previous-scene quote is valid only while the two scenes are adjacent."""
    from sqlalchemy.orm import aliased

    destination = aliased(Scene)
    query = (
        select(ShotScriptSource, Scene, destination.script_position)
        .join(Scene, Scene.id == ShotScriptSource.scene_id)
        .join(Shot, Shot.id == ShotScriptSource.shot_id)
        .join(destination, destination.id == Shot.scene_id)
        .where(Scene.script_id == script_id, Shot.scene_id != Scene.id)
    )
    if origin_id:
        query = query.where(Scene.id == origin_id)
    texts = {}
    for row, origin, position in db.execute(query):
        if origin.id not in texts:
            texts[origin.id] = passages(origin.script_blocks)
        adjacent = position and origin.script_position == position - 1
        row.current_text = texts[origin.id].get(row.block_id) if adjacent else None


def revision(rows):
    values = sorted(
        (str(row.scene_id), row.block_id, row.accepted_text, row.current_text) for row in rows
    )
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def affected(db, project_id, script_id, shot_id=None):
    query = (
        select(ShotScriptSource, Shot, Scene)
        .join(Shot, Shot.id == ShotScriptSource.shot_id)
        .join(Scene, Scene.id == ShotScriptSource.scene_id)
        .where(
            Scene.script_id == script_id,
            Scene.project_id == project_id,
            Shot.project_id == project_id,
            Shot.deleted_at.is_(None),
            ShotScriptSource.accepted_text.is_distinct_from(ShotScriptSource.current_text),
        )
        .order_by(Scene.ordinal, Shot.ordinal, Shot.id, ShotScriptSource.block_id)
    )
    if shot_id:
        query = query.where(Shot.id == shot_id)
    groups = {}
    for row, shot, source in db.execute(query):
        entry = groups.setdefault(
            shot.id,
            {
                "shot_id": shot.id,
                "scene_id": shot.scene_id,
                "title": shot.title or shot.code,
                "changes": [],
                "rows": [],
            },
        )
        entry["rows"].append(row)
        entry["changes"].append(
            {
                "scene_title": source.title,
                "scene_level": not row.block_id,
                "before": row.accepted_text,
                "after": row.current_text,
            }
        )
    for entry in groups.values():
        entry["revision"] = revision(entry.pop("rows"))
    return list(groups.values())


def confirm(db, ctx, script_id, shot_id, expected, *, require_changes=False):
    script = db.scalar(
        select(Script)
        .where(
            Script.id == script_id, Script.project_id == ctx.project.id, Script.deleted_at.is_(None)
        )
        .with_for_update()
    )
    if not script:
        raise NotFound("剧本不存在")
    shot = db.scalar(
        select(Shot)
        .join(Scene, Scene.id == Shot.scene_id)
        .where(
            Shot.id == shot_id,
            Shot.project_id == ctx.project.id,
            Shot.deleted_at.is_(None),
            Scene.script_id == script.id,
        )
        .with_for_update(of=Shot)
    )
    if not shot:
        raise NotFound("镜头不存在")
    rows = db.scalars(
        select(ShotScriptSource)
        .join(Scene, Scene.id == ShotScriptSource.scene_id)
        .where(
            ShotScriptSource.shot_id == shot.id,
            Scene.script_id == script.id,
            ShotScriptSource.accepted_text.is_distinct_from(ShotScriptSource.current_text),
        )
    ).all()
    if (require_changes and not rows) or (rows and revision(rows) != expected):
        raise Conflict("剧本又有修改，请查看最新差异后确认")
    for row in rows:
        row.accepted_text = row.current_text
    db.flush()


def generation_source(db, shot_id, *, current=False):
    rows = db.scalars(select(ShotScriptSource).where(ShotScriptSource.shot_id == shot_id)).all()
    if not rows:
        return None
    # Record the accepted inputs even if the script changed while the prompt was under review.
    values = sorted(
        (str(row.scene_id), row.block_id, row.current_text if current else row.accepted_text)
        for row in rows
    )
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def _shot_revision(shot):
    from app.modules.narrative.tool_tasks import fingerprint

    return fingerprint([shot.title, shot.description, shot.prompt_override, shot.storyboard])


def propose_prompts(db, ctx, script_id, shot_id, expected, strategy_name):
    from app.core.errors import ProviderError
    from app.modules.narrative import service
    from app.modules.shot import breakdown

    changes = affected(db, ctx.project.id, script_id, shot_id)
    if not changes or changes[0]["revision"] != expected:
        raise Conflict("剧本已更新，请重新查看差异")
    shot = db.get(Shot, shot_id)
    scene = db.get(Scene, shot.scene_id)
    if scene.script_position is None:
        raise Conflict("这场戏已移出剧本，可保留现有镜头")
    if not any(c["after"] for c in changes[0]["changes"]):
        raise Conflict("这段来源已移除，请在镜头详情核对是否保留")
    shot_revision = _shot_revision(shot)
    board = shot.storyboard or {}
    revised = "\n".join(c["after"] for c in changes[0]["changes"] if c["after"])
    strategy = service._strategy(db, ctx.project.id, strategy_name)
    if strategy.name == "mock":
        still = motion = revised
    else:
        text = (
            f"{scene.title}\n{scene.body}\n\n只改写一个已存在的镜头，返回一项。"
            f"镜头标题：{shot.title or shot.code}\n新版依据：{revised}\n"
            f"原静帧描述（保留仍适用的构图）：{board.get('still_prompt') or shot.description}\n"
            f"原动态描述（保留仍适用的运镜）：{board.get('motion_prompt') or ''}"
        )
        drafts = breakdown.suggest(
            db, strategy, ctx.project.id, text, scene.adapted_from_chapter_id, scene=scene
        )
        if len(drafts) != 1:
            raise ProviderError("未取得单个镜头的有效提示词，请重试")
        still, motion = drafts[0].still_prompt.strip(), drafts[0].motion_prompt.strip()
    if not still or not motion or max(len(still), len(motion)) > 12000:
        raise ProviderError("模型返回的提示词无效，请重试")
    return {
        "revision": expected,
        "shot_revision": shot_revision,
        "still_prompt": still,
        "motion_prompt": motion,
    }


def adopt_prompts(db, ctx, script_id, shot_id, proposal):
    from app.core.errors import Locked

    # Lock and recheck the source and shot together, exactly as for a manual review.
    confirm(db, ctx, script_id, shot_id, proposal.revision, require_changes=True)
    shot = db.get(Shot, shot_id)
    if shot.status == "locked":
        raise Locked("该镜头已锁定")
    if _shot_revision(shot) != proposal.shot_revision:
        raise Conflict("镜头已被编辑，请重新核对后采用")
    still, motion = proposal.still_prompt.strip(), proposal.motion_prompt.strip()
    if not still or not motion:
        raise Conflict("画面和动态提示词需要有内容")
    shot.storyboard = {**(shot.storyboard or {}), "still_prompt": still, "motion_prompt": motion}
    db.flush()
