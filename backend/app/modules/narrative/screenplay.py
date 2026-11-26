"""Store each screenplay scene once and keep its production identity across edits."""

from collections import defaultdict

from sqlalchemy import func, select

from app.core.errors import Conflict, Locked
from app.models.identity import Project
from app.models.narrative import Scene, render_scene_body
from app.platform import coding


def sections(blocks):
    group = []
    for block in blocks:
        if block["block_type"] == "scene_heading" and group:
            yield group
            group = []
        group.append(block)
    if group:
        yield group


def anchor(group):
    first = group[0]
    return "heading/" + first["id"] if first["block_type"] == "scene_heading" else "preamble"


def title(group, position):
    return group[0]["text"] if group[0]["block_type"] == "scene_heading" else f"场景 {position}"


def locked_scenes(db, script):
    return list(
        db.scalars(
            select(Scene)
            .where(Scene.script_id == script.id, Scene.deleted_at.is_(None))
            .order_by(Scene.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def write(db, script, blocks, elements_by_anchor=None):
    """Caller holds the script lock. Existing shots, art and revisions keep their IDs."""
    from app.modules.asset.story_sources import AssetNames, scope_for
    from app.modules.narrative import scene_elements, script_sources

    rows = locked_scenes(db, script)
    old_order = [
        r.id
        for r in sorted(
            (r for r in rows if r.script_position is not None), key=lambda r: r.script_position
        )
    ]
    by_anchor = {row.script_anchor: row for row in rows if row.script_anchor}
    historical = defaultdict(list)
    for row in rows:
        if row.script_anchor is None and row.adapted_from_chapter_id == script.source_chapter_id:
            historical[(row.title, row.body or "")].append(row)
    project = db.scalar(select(Project).where(Project.id == script.project_id).with_for_update())
    present = set()
    ordered = []
    changes = []
    for position, group in enumerate(sections(blocks), 1):
        key = anchor(group)
        heading = title(group, position)
        body = render_scene_body(group)
        row = by_anchor.get(key)
        if row is None:
            candidates = historical[(heading[:255], body)]
            if len(candidates) == 1:
                row = candidates.pop()
        if row is None:
            row = Scene(
                project_id=project.id,
                script_id=script.id,
                code=coding.next_code(db, project, "scene"),
                created_by=script.created_by,
                adapted_from_chapter_id=script.source_chapter_id,
            )
            db.add(row)
        if row.id and row.status == "locked" and row.script_blocks != group:
            raise Locked("这场戏已锁定，请解锁后修改正文")
        if row.script_blocks != group or row.script_position is None:
            changes.append((row, row.script_blocks, group))
        if not row.summary or row.summary == (row.body or "")[:30].replace("\n", " "):
            row.summary = body[:30].replace("\n", " ")
        row.script_anchor = key
        row.script_position = row.ordinal = position
        row.script_blocks = group
        row.title = heading[:255]
        row.legacy_body = ""
        ordered.append(row)
        present.add(key)
    for row in rows:
        if row.script_position is not None and row.script_anchor not in present:
            if row.status == "locked":
                raise Locked("已锁定的场次仍在剧本中，请解锁后移除")
            changes.append((row, row.script_blocks, None))
            # Keep its last text and playable work available from the storyboard.
            row.script_position = None
    script.legacy_blocks = []
    db.flush()
    names = None
    for row, before, after in changes:
        script_sources.update_scene(db, row, before, after)
        if after:
            if names is None:
                names = AssetNames(
                    db, script.project_id, scope_for(db, script.id, script.source_chapter_id)
                )
            scene_elements.sync(db, row, (elements_by_anchor or {}).get(row.script_anchor), names)
    reordered = old_order != [r.id for r in ordered]
    if reordered:
        script_sources.refresh_continuity(db, script.id)
    if changes or reordered:
        script.updated_at = func.now()
    db.flush()
    db.expire(script, ["screenplay"])


def assert_unique_ids(blocks):
    ids = [b["id"] for b in blocks]
    if len(set(ids)) != len(ids) or any(len(bid) > 256 for bid in ids):
        raise Conflict("剧本中有重复或无效的段落编号，请重新载入后保存")


def edit_scene(db, ctx, scene, values):
    """Apply the scene assistant's plain-text edits to the same structured document."""
    import uuid
    from difflib import SequenceMatcher

    from app.models.narrative import Script
    from app.modules.narrative.service import update_script_blocks

    script = db.get(Script, scene.script_id)
    summary_changed = values["summary"] != scene.summary
    group = list(scene.script_blocks)
    has_heading = group[0]["block_type"] == "scene_heading"
    headings = group[:1] if has_heading else []
    lines = group[1:] if has_heading else group
    if values["title"] != scene.title:
        heading = (values["title"] or "").strip()
        if not heading:
            raise Conflict("请为场次保留标题")
        if not headings:
            raise Conflict("请在剧本编辑器中为开场段落插入场次标题")
        headings = [{**headings[0], "text": heading}]
    if values["body"] != scene.body:
        new_lines = [s.strip() for s in (values["body"] or "").splitlines() if s.strip()]
        edits = SequenceMatcher(
            None, [render_scene_body([b]) for b in lines], new_lines, autojunk=False
        )
        body = []
        for kind, start, stop, first, last in edits.get_opcodes():
            if kind == "equal":
                body.extend(lines[start:stop])
                continue
            for i, text in enumerate(new_lines[first:last]):
                old = lines[start + i] if kind == "replace" and start + i < stop else None
                block_type = old["block_type"] if old else "action"
                if block_type == "character":
                    text = text.rstrip(":：")
                if block_type == "parenthetical":
                    text = text.strip("()")
                body.append(
                    {
                        "id": old["id"] if old else uuid.uuid4().hex,
                        "block_type": block_type,
                        "text": text,
                    }
                )
        lines = body
    blocks = [
        block
        for row in script.screenplay
        for block in (headings + lines if row.id == scene.id else row.script_blocks)
    ]
    update_script_blocks(db, ctx, script.id, blocks, script.content_revision)
    if summary_changed:
        scene.summary = values["summary"]
