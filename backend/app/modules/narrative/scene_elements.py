"""The scene roster exists before storyboard shots. Artwork choices belong to the use."""

import re

from sqlalchemy import select

from app.adapters.contracts import EntityDraft
from app.core.errors import Conflict, NotFound
from app.models.asset import Asset, AssetReferenceImage
from app.models.narrative import Chapter, Scene, SceneElement
from app.modules.asset.story_sources import AssetNames, normalized, scope_for


def roster(db, scene_id):
    return list(
        db.scalars(
            select(SceneElement)
            .where(SceneElement.scene_id == scene_id, SceneElement.active.is_(True))
            .order_by(SceneElement.kind, SceneElement.name)
        )
    )


def seed_elements(blocks):
    for block in blocks:
        text, kind = block["text"], block["block_type"]
        if kind == "character":
            if re.search(r"画外|旁白|电话音|\b(?:V\.?O|O\.?S)\b", text, re.I):
                continue
            name = re.split(r"[（(]", text, maxsplit=1)[0].strip()
            role = "character"
        elif kind == "scene_heading":
            name = re.sub(
                r"^(?:INT\.?/EXT\.?|INT\.?|EXT\.?|内景|外景)\s*[:.：-]?\s*", "", text, flags=re.I
            )
            name = re.sub(
                r"\s*[-－—· ]\s*(?:日|夜|清晨|黎明|早晨|上午|白天|午后|下午|黄昏|傍晚|深夜"
                r"|DAY|NIGHT|DAWN|DUSK)$",
                "",
                name,
                flags=re.I,
            ).strip()
            role = "location"
        else:
            continue
        if name and len(name) <= 255:
            yield EntityDraft(type=role, name=name, evidence=text, summary=text)


def visible_lines(scene):
    if scene.script_blocks is None:
        return [v for v in [scene.title, *(scene.body or "").splitlines()] if v]
    return [
        b["text"]
        for b in scene.script_blocks
        if b["block_type"] in {"character", "scene_heading", "action"}
        and not (
            b["block_type"] == "character"
            and re.search(r"画外|旁白|\b(?:V\.?O|O\.?S)\b", b["text"], re.I)
        )
    ]


def sync(db, scene, suggestions=None, names=None, *, partial=False):
    from app.modules.narrative.tool_tasks import fingerprint

    rows = {
        (r.kind, r.name_key): r
        for r in db.scalars(select(SceneElement).where(SceneElement.scene_id == scene.id))
    }
    lines = visible_lines(scene)
    seen = set()
    if names is None:
        names = AssetNames(
            db, scene.project_id, scope_for(db, scene.script_id, scene.adapted_from_chapter_id)
        )
    candidates = [*seed_elements(scene.script_blocks or []), *(suggestions or [])]
    for index, draft in enumerate(candidates):
        name, evidence = draft.name.strip(), draft.evidence.strip()
        if (
            not name
            or len(name) > 255
            or not evidence
            or not any(evidence in line for line in lines)
        ):
            continue
        key = (draft.type, normalized(name))
        row = rows.get(key)
        if row is None:
            row = SceneElement(scene_id=scene.id, kind=draft.type, name=name, name_key=key[1])
            rows[key] = row
            db.add(row)
        row.active = True
        # A heading/speaker label must not overwrite a previously grounded visual description.
        inferred = suggestions is not None and index >= len(candidates) - len(suggestions)
        if inferred or not row.prompt or not any(row.evidence in line for line in lines):
            row.evidence = evidence
            row.prompt = draft.summary.strip() or evidence
        if not row.asset_id and not row.manual_binding:
            match = names.visible_subject(draft.type, name, evidence)
            if match:
                row.asset_id = match.id
        seen.add(key)
    for key, row in rows.items():
        if key not in seen and (
            (suggestions is not None and not partial)
            or not any(row.evidence in line for line in lines)
        ):
            row.active = False
    if not partial:
        scene.elements_revision = (
            fingerprint(scene.script_blocks) if suggestions is not None else None
        )
    db.flush()


def extract_for_script(db, script, strategy, text):
    """Show grounded scene uses in the script's Extract Assets tool, too."""
    from collections import defaultdict

    from app.modules.narrative.schemas import EntityDraftOut
    from app.modules.narrative.screenplay import locked_scenes
    from app.modules.narrative.tool_tasks import fingerprint

    scenes = list(script.screenplay)
    unresolved = []
    if any(row.elements_revision != fingerprint(row.script_blocks) for row in scenes):
        revision = script.content_revision
        drafts = strategy.extract_entities(text)
        db.refresh(script, with_for_update=True)
        locked_scenes(db, script)
        db.expire(script, ["screenplay"])
        if script.content_revision != revision:
            raise Conflict("剧本已修改，请按照现稿重新提取")
        scenes = list(script.screenplay)
        lines = {s.id: visible_lines(s) for s in scenes}
        matched = defaultdict(list)
        for draft in drafts:
            quote = draft.evidence.strip()
            owners = [
                sid
                for sid, stage in lines.items()
                if quote and any(quote in line for line in stage)
            ]
            if len(owners) == 1:
                matched[owners[0]].append(draft)
            else:
                unresolved.append(EntityDraftOut(**draft.model_dump()))
        if matched:
            names = AssetNames(
                db, script.project_id, scope_for(db, script.id, script.source_chapter_id)
            )
            for s in scenes:
                if s.id in matched:
                    db.refresh(s, with_for_update=True)
                    sync(db, s, matched[s.id], names, partial=True)
    result = []
    rows = db.execute(
        select(SceneElement, Scene)
        .join(Scene, Scene.id == SceneElement.scene_id)
        .where(
            Scene.script_id == script.id,
            Scene.script_position.is_not(None),
            Scene.deleted_at.is_(None),
            SceneElement.active.is_(True),
        )
        .order_by(Scene.script_position, SceneElement.kind, SceneElement.name)
    )
    for member, scene in rows:
        result.append(
            EntityDraftOut(
                type=member.kind,
                name=member.name,
                summary=member.direction or member.prompt,
                evidence=member.evidence,
                scene_id=scene.id,
                element_id=member.id,
                scene_title=f"{scene.script_position} · {scene.title}",
                asset_id=member.asset_id,
            )
        )
    return [*result, *unresolved]


def prepare(db, scene, strategy):
    """Run once per saved scene revision, including scenes that genuinely have no subjects."""
    from app.modules.narrative.tool_tasks import fingerprint
    from app.modules.shot.breakdown import source_excerpt

    if not scene or scene.script_position is None:
        return []
    expected = fingerprint(scene.script_blocks)
    if scene.elements_revision != expected:
        text = f"本场剧本\n{scene.title}\n{scene.body}"
        if scene.adapted_from_chapter_id:
            chapter = db.get(Chapter, scene.adapted_from_chapter_id)
            if chapter:
                excerpt = source_excerpt(chapter.content, text)
                text += f"\n原著线索（仅核对外观与身份）\n{excerpt}"
        drafts = strategy.extract_entities(text)
        with db.begin_nested():
            db.refresh(scene, with_for_update=True)
            if scene.script_position is None or expected != fingerprint(scene.script_blocks):
                raise Conflict("本场剧本已修改，请使用最新版本拆镜")
            if scene.elements_revision != expected:
                sync(db, scene, drafts)
    return roster(db, scene.id)


def update_look(db, ctx, scene_id, element_id, data):
    scene = db.scalar(
        select(Scene)
        .where(Scene.id == scene_id, Scene.project_id == ctx.project.id, Scene.deleted_at.is_(None))
        .with_for_update()
    )
    if not scene:
        raise NotFound("找不到这场戏")
    row = db.get(SceneElement, element_id)
    if not row or row.scene_id != scene.id or not row.active:
        raise NotFound("找不到这项场次素材")
    from app.models.enums import VersionStatus

    if scene.status == VersionStatus.locked:
        raise Conflict("这场戏已锁定")
    previous = row.asset_id
    if "asset_id" in data.model_fields_set:
        asset = db.get(Asset, data.asset_id) if data.asset_id else None
        if data.asset_id and (
            not asset
            or asset.project_id != ctx.project.id
            or asset.deleted_at
            or asset.type != row.kind
        ):
            raise Conflict("请选择项目中同类型的素材")
        if row.asset_id != data.asset_id:
            row.reference_hash = None
        row.asset_id = data.asset_id
        row.manual_binding = True
    if "reference_hash" in data.model_fields_set:
        value = data.reference_hash
        asset = db.get(Asset, row.asset_id) if row.asset_id else None
        if value and (
            not asset
            or asset.deleted_at
            or (
                asset.representative_blob_hash != value
                and not db.scalar(
                    select(AssetReferenceImage.id)
                    .where(
                        AssetReferenceImage.asset_id == asset.id,
                        AssetReferenceImage.blob_hash == value,
                    )
                    .limit(1)
                )
            )
        ):
            raise Conflict("请选择这项素材中的参考图")
        row.reference_hash = value
    if data.direction is not None:
        row.direction = data.direction.strip()
    db.flush()
    if previous != row.asset_id:
        _rebind_visible_shots(db, row)
    return row


def adopt(db, ctx, scene_id, element_id, direction=None):
    from app.models.enums import AssetType
    from app.modules.asset.story_sources import resolve
    from app.modules.narrative.schemas import SceneElementEdit
    from app.modules.shot.service import _scene

    scene = _scene(db, ctx.project.id, scene_id, lock=True)
    member = db.get(SceneElement, element_id)
    if not member or member.scene_id != scene.id or not member.active:
        raise NotFound("找不到这项场次素材")
    if scene.status == "locked":
        raise Conflict("这场戏已锁定")
    if direction is not None:
        member.direction = direction.strip()
    asset = db.get(Asset, member.asset_id) if member.asset_id else None
    if asset and not asset.deleted_at:
        return member
    asset = resolve(
        db,
        ctx,
        AssetType(member.kind),
        member.name,
        member.direction or member.prompt,
        source_type="scene",
        source_id=scene.id,
        scope=scope_for(db, scene.script_id, scene.adapted_from_chapter_id),
        evidence=member.evidence,
    )
    return update_look(db, ctx, scene_id, element_id, SceneElementEdit(asset_id=asset.id))


def _rebind_visible_shots(db, member):
    from app.models.shot import Shot, ShotAssetRef

    for shot in db.scalars(
        select(Shot)
        .where(Shot.scene_id == member.scene_id, Shot.deleted_at.is_(None))
        .order_by(Shot.id)
        .with_for_update()
    ):
        refs = list(db.scalars(select(ShotAssetRef).where(ShotAssetRef.shot_id == shot.id)))
        stored = next(
            (
                r
                for r in refs
                if r.ref_mode == "floating"
                and (r.usage or {}).get("scene_element_id") == str(member.id)
            ),
            None,
        )
        board = dict(shot.storyboard or {})
        pending = list(board.get("unresolved_subjects", []))
        matching = [
            s
            for s in pending
            if s.get("type") == member.kind
            and normalized(s.get("name", "")) == member.name_key
            and s.get("evidence")
        ]
        if not stored and not matching:
            continue
        if member.asset_id and str(member.asset_id) not in board.get("suppressed_asset_ids", []):
            duplicate = next(
                (r for r in refs if r.asset_id == member.asset_id and r.role == member.kind), None
            )
            if stored and stored != duplicate and duplicate:
                db.delete(stored)
            elif not duplicate:
                if not stored:
                    stored = ShotAssetRef(
                        shot_id=shot.id,
                        role=member.kind,
                        ordinal=len(refs),
                        usage={
                            **{
                                k: v
                                for k, v in matching[0].items()
                                if k in {"evidence", "look", "aspects"}
                            },
                            "scene_element_id": str(member.id),
                        },
                    )
                    db.add(stored)
                stored.asset_id = member.asset_id
            pending = [s for s in pending if s not in matching]
        elif stored:
            pending.append(
                {
                    "type": member.kind,
                    "name": member.name,
                    "evidence": stored.usage.get("evidence", ""),
                }
            )
            db.delete(stored)
        if pending != board.get("unresolved_subjects", []):
            shot.storyboard = {**board, "unresolved_subjects": pending}
    db.flush()
