"""Shared shot drafts for chapter, script and storyboard entry points."""

import re
from collections import Counter

from sqlalchemy import select

from app.core.errors import ProviderError
from app.models.narrative import Chapter
from app.models.shot import Shot, ShotAssetRef
from app.modules.asset.story_sources import AssetNames, scope_for
from app.modules.narrative import script_sources


def source_excerpt(content, scene, limit=1600):
    if len(content) <= limit:
        return content
    paragraphs = [
        p[start : start + 400]
        for p in content.splitlines()
        if p.strip()
        for start in range(0, len(p), 400)
    ]
    if not paragraphs:
        return ""

    def terms(text):
        return Counter(re.findall(r"(?=([\u3400-\u9fff]{2}))|([a-zA-Z]{3,})", text.lower()))

    wanted = terms(scene)

    def score(index):
        return sum((wanted & terms(paragraphs[index])).values())

    best = max(range(len(paragraphs)), key=score)
    if not score(best):
        return ""
    return "\n".join(paragraphs[max(0, best - 1) : best + 2])[:limit]


def scene_context(db, scene, text, strategy):
    if not scene or not scene.script_position:
        return text
    from app.modules.narrative import scene_elements
    elements = scene_elements.prepare(db, scene, strategy)
    if strategy.name != "cloud_llm":
        return text
    previous = script_sources.previous_scene(db, scene)
    if previous:
        ending = (previous.body or "")[-700:]
        text = f"上场结尾（仅供接戏）\n{previous.title}\n{ending}\n\n本场剧本\n{text}"
    if elements:
        descriptions = "\n".join(f"{e.name}（{e.kind}） {e.direction or e.prompt}" for e in elements)
        text += f"\n本场已记录的素材（只引用本镜实际入画的项）\n{descriptions}"
    return text


def suggest(db, strategy, project_id, text, chapter_id=None, *, scene=None):
    context = scene_context(db, scene, text, strategy)
    excerpt = ""
    if strategy.name == "cloud_llm" and chapter_id:
        from app.modules.setting.service import settings_context

        chapter = db.get(Chapter, chapter_id)
        if chapter:
            excerpt = source_excerpt(chapter.content or "", text)
            facts = settings_context(
                db, project_id, chapter.novel_id, chapter.ordinal, max_chars=1000, subject_text=text
            )
            context = (
                f"本场剧本\n{context}\n对应原文章节 {chapter.ordinal}\n{excerpt}\n已知设定\n{facts}"
            )
    drafts = strategy.suggest_storyboard_shots(context)
    if strategy.name == "cloud_llm":
        if any(not shot.still_prompt.strip() or not shot.motion_prompt.strip() for shot in drafts):
            raise ProviderError("模型返回的分镜缺少画面或动作描述，请重试此项")
        evidence = "".join((text + "\n" + excerpt).split())
        for draft in drafts:
            for subject in draft.subjects:
                if "".join(subject.evidence.split()) not in evidence:
                    subject.evidence = ""
    return drafts


def save_draft(db, shot, suggestion, names):
    from app.models.narrative import Scene
    from app.models.asset import Asset
    from app.modules.asset.story_sources import normalized
    from app.modules.narrative.scene_elements import roster, visible_lines

    scene = db.get(Scene, shot.scene_id)
    script_sources.attach(db, shot, scene, suggestion)
    members = roster(db, scene.id) if scene.script_position is not None else []
    stage = ["".join(line.split()) for line in visible_lines(scene)] if members else []
    fields = suggestion.model_dump(
        exclude={"title", "description", "subjects", "source_quotes", "continuity_quotes"}, exclude_none=True
    )
    board = {
        **{k: v for k, v in fields.items() if v != ""},
        **{k: v for k, v in (shot.storyboard or {}).items() if v != ""},
    }
    pending = []
    linked = set(db.scalars(select(ShotAssetRef.asset_id).where(ShotAssetRef.shot_id == shot.id)))
    for subject in suggestion.subjects:
        asset = names.visible_subject(subject.type, subject.name, subject.evidence)
        element = next((e for e in members if e.kind == subject.type and
                        (e.name_key == normalized(subject.name) or (asset and e.asset_id == asset.id))), None)
        if scene.script_position is not None:
            quote = "".join(subject.evidence.split())
            grounded = quote and any(quote in line for line in stage)
            asset = db.get(Asset, element.asset_id) if element and element.asset_id and grounded else None
            if not grounded:
                subject.evidence = ""
            if asset and asset.deleted_at:
                asset = None
        if not asset:
            pending.append(subject.model_dump())
            continue
        if asset.id in linked or str(asset.id) in board.get("suppressed_asset_ids", []):
            continue
        linked.add(asset.id)
        db.add(
            ShotAssetRef(
                shot_id=shot.id,
                asset_id=asset.id,
                role=subject.type,
                usage={**subject.model_dump(exclude={"name", "type"}),
                       **({"scene_element_id": str(element.id)} if element else {})},
                ordinal=len(linked) - 1,
            )
        )
    if pending:
        board["unresolved_subjects"] = pending
    shot.storyboard = board


def next_shot_numbers(rows, scene_code):
    """Keep display order and public codes independent after a reorder or removal."""
    prefix = f"{scene_code}-SH"
    last_ordinal = last_code = 0
    for ordinal, code in rows:
        last_ordinal = max(last_ordinal, ordinal)
        suffix = code.removeprefix(prefix)
        if code.startswith(prefix) and suffix.isascii() and suffix.isdigit():
            last_code = max(last_code, int(suffix))
    return last_ordinal + 1, max(last_code, last_ordinal) + 1


def next_scene_shot_numbers(db, scene):
    rows = db.execute(
        select(Shot.ordinal, Shot.code).where(
            Shot.project_id == scene.project_id, Shot.scene_id == scene.id
        )
    )
    return next_shot_numbers(rows, scene.code)


def create_drafts(db, ctx, scene, suggestions):
    from app.models.identity import Project

    db.scalar(select(Project.id).where(Project.id == ctx.project.id).with_for_update())
    names = AssetNames(
        db, ctx.project.id, scope_for(db, scene.script_id, scene.adapted_from_chapter_id)
    )
    ordinal, code_number = next_scene_shot_numbers(db, scene)
    for i, draft in enumerate(suggestions):
        shot = Shot(
            project_id=ctx.project.id,
            scene_id=scene.id,
            ordinal=ordinal + i,
            code=f"{scene.code}-SH{code_number + i:03d}",
            title=draft.title or f"镜头 {ordinal + i}",
            description=draft.description,
            created_by=ctx.user.id,
        )
        db.add(shot)
        db.flush()
        save_draft(db, shot, draft, names)
