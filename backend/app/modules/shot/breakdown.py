"""Shared shot drafts for chapter, script and storyboard entry points."""

import re
from collections import Counter

from sqlalchemy import select

from app.core.errors import ProviderError
from app.models.narrative import Chapter
from app.models.shot import Shot, ShotAssetRef
from app.modules.asset.story_sources import AssetNames, scope_for


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


def suggest(db, strategy, project_id, text, chapter_id=None):
    context = text
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
                f"本场剧本\n{text}\n对应原文章节 {chapter.ordinal}\n{excerpt}\n已知设定\n{facts}"
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
    fields = suggestion.model_dump(exclude={"title", "description", "subjects"}, exclude_none=True)
    board = {
        **{k: v for k, v in fields.items() if v != ""},
        **{k: v for k, v in (shot.storyboard or {}).items() if v != ""},
    }
    pending = []
    linked = set(db.scalars(select(ShotAssetRef.asset_id).where(ShotAssetRef.shot_id == shot.id)))
    for subject in suggestion.subjects:
        asset = names.visible_subject(subject.type, subject.name, subject.evidence)
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
                usage=subject.model_dump(exclude={"name", "type"}),
                ordinal=len(linked) - 1,
            )
        )
    if pending:
        board["unresolved_subjects"] = pending
    shot.storyboard = board


def create_drafts(db, ctx, scene, suggestions):
    from app.models.identity import Project

    db.scalar(select(Project.id).where(Project.id == ctx.project.id).with_for_update())
    names = AssetNames(
        db, ctx.project.id, scope_for(db, scene.script_id, scene.adapted_from_chapter_id)
    )
    for i, draft in enumerate(suggestions, 1):
        shot = Shot(
            project_id=ctx.project.id,
            scene_id=scene.id,
            ordinal=i,
            code=f"{scene.code}-SH{i:03d}",
            title=draft.title or f"镜头 {i}",
            description=draft.description,
            created_by=ctx.user.id,
        )
        db.add(shot)
        db.flush()
        save_draft(db, shot, draft, names)
