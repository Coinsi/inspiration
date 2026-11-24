"""setting 业务:设定 CRUD + 一键全书后台提取(逐章增量合并)+ 转资产。

全书提取在**后台线程**中跑(本地 CELERY_EAGER 环境同样可用):
每章调用拆解策略 extract_settings(章节正文, 已有设定名录) → 同名条目整条覆盖(AI 给出
合并后的完整版),新名字新建;每章提交一次,进度可轮询、可取消、中断后重发即续跑
(已有名录会让 AI 不再重复产出已完整的设定)。
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import AppError, Conflict, NotFound
from app.models.enums import AssetType
from app.models.narrative import Chapter, Novel
from app.models.setting import Setting, SettingExtraction
from app.modules.narrative.service import _strategy
from app.modules.setting import schemas

# 设定分类 → 资产类型(可转资产的三类)
_CATEGORY_TO_ASSET = {
    "character": AssetType.character,
    "location": AssetType.location,
    "item": AssetType.prop,
}


# ── CRUD ──
def list_settings(
    db: Session,
    project_id: uuid.UUID,
    novel_id: uuid.UUID | None = None,
    category: str | None = None,
) -> list[Setting]:
    stmt = select(Setting).where(Setting.project_id == project_id)
    if novel_id:
        stmt = stmt.where(Setting.novel_id == novel_id)
    if category:
        stmt = stmt.where(Setting.category == category)
    return list(db.scalars(stmt.order_by(Setting.category, Setting.ordinal, Setting.created_at)))


def _get(db: Session, project_id: uuid.UUID, setting_id: uuid.UUID) -> Setting:
    s = db.get(Setting, setting_id)
    if s is None or s.project_id != project_id:
        raise NotFound("设定不存在")
    return s


def create_setting(db: Session, ctx: ProjectContext, data: schemas.SettingIn) -> Setting:
    from app.modules.narrative.service import get_novel

    if data.novel_id:
        get_novel(db, ctx.project.id, data.novel_id)
    if not data.name.strip():
        raise Conflict("设定名称不能为空")
    s = Setting(
        project_id=ctx.project.id,
        novel_id=data.novel_id,
        category=data.category,
        name=data.name.strip(),
        content=data.content,
        created_by=ctx.user.id,
    )
    db.add(s)
    db.flush()
    return s


def update_setting(
    db: Session, ctx: ProjectContext, setting_id: uuid.UUID, data: schemas.SettingUpdate
) -> Setting:
    s = _get(db, ctx.project.id, setting_id)
    if data.category is not None:
        s.category = data.category
    if data.name is not None:
        if not data.name.strip():
            raise Conflict("设定名称不能为空")
        s.name = data.name.strip()
    if data.content is not None:
        s.content = data.content
        s.source_notes = {**(s.source_notes or {}), "edited": True}
    db.flush()
    return s


def delete_setting(db: Session, ctx: ProjectContext, setting_id: uuid.UUID) -> None:
    s = _get(db, ctx.project.id, setting_id)
    db.delete(s)
    db.flush()


def to_asset(db: Session, ctx: ProjectContext, setting_id: uuid.UUID, asset_id=None, separate=False):
    """人物/地点/物品类设定 → 落为资产(接通既有资产链路)。"""
    s = _get(db, ctx.project.id, setting_id)
    asset_type = _CATEGORY_TO_ASSET.get(s.category)
    if asset_type is None:
        raise Conflict(f"该分类({s.category})不支持转为资产(支持:{','.join(_CATEGORY_TO_ASSET)})")
    from app.modules.asset.story_sources import resolve

    a = resolve(
        db,
        ctx,
        asset_type,
        s.name,
        s.content,
        source_type="setting",
        source_id=s.id,
        scope=s.novel_id,
        chosen=asset_id,
        separate=separate,
    )
    audit.record(
        db,
        action="setting.to_asset",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="setting",
        target_id=s.id,
        detail={"asset_id": str(a.id)},
    )
    return a


# ── 设定上下文(S3:供剧本生成/AI 助手注入) ──
_GLOBAL_CATEGORIES = ("world", "power_system")  # 全局规则类:无论哪章都注入

_CATEGORY_LABEL = {
    "world": "世界观",
    "power_system": "力量体系",
    "faction": "势力组织",
    "character": "人物",
    "location": "地点",
    "item": "物品道具",
    "glossary": "术语",
    "timeline": "大事记",
}


def _known_content(setting, ordinal):
    if ordinal is None or (setting.source_notes or {}).get("edited"):
        return setting.content
    chapters = setting.source_chapters or []
    if not chapters or max(chapters) <= ordinal:
        return setting.content
    notes = setting.source_notes or {}
    return "\n".join(
        notes[str(n)]["fact"]
        for n in sorted(chapters)
        if n <= ordinal and str(n) in notes and notes[str(n)].get("fact")
    )


def settings_context(
    db: Session,
    project_id: uuid.UUID,
    novel_id: uuid.UUID,
    chapter_ordinal: int | None = None,
    max_chars: int = 3500,
    *,
    subject_text="",
) -> str:
    rows = list_settings(db, project_id, novel_id=novel_id)

    def relevant(s):
        if s.name and s.name in subject_text:
            return 0
        if chapter_ordinal is None or chapter_ordinal in (s.source_chapters or []):
            return 1
        return 2 if s.category in _GLOBAL_CATEGORIES else 3

    lines, total = [], 0
    for s in sorted(rows, key=relevant):
        content = _known_content(s, chapter_ordinal)
        if relevant(s) == 3 or not content:
            continue
        line = f"【{_CATEGORY_LABEL.get(s.category, s.category)}】{s.name}：{content[:240]}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


# ── 设定合并 upsert(提取线程使用;ordinals 为该条设定本次涉及的章节序号) ──
def _upsert_draft(
    db: Session,
    project_id: uuid.UUID,
    novel_id: uuid.UUID,
    draft,
    ordinals: list[int],
    user_id: uuid.UUID | None,
    notes: dict | None = None,
) -> None:
    name = (draft.name or "").strip()
    if not name:
        return
    row = db.scalar(
        select(Setting).where(
            Setting.project_id == project_id,
            Setting.novel_id == novel_id,
            Setting.category == draft.category,
            Setting.name == name,
        )
    )
    if row is None:
        row = Setting(
            project_id=project_id,
            novel_id=novel_id,
            category=draft.category,
            name=name,
            content=draft.content,
            created_by=user_id,
        )
        db.add(row)
    elif not (row.source_notes or {}).get("edited"):
        latest = max(ordinals, default=0)
        later = [n for n in (row.source_chapters or []) if n > latest]
        # A recovered earlier chapter must preserve facts already learned downstream.
        later_facts = [(row.source_notes or {}).get(str(n), {}).get("fact") for n in sorted(later)]
        if later and not all(later_facts):
            later_facts.append(row.content)
        row.content = "\n".join(
            dict.fromkeys(part for part in (draft.content, *later_facts) if part)
        )
    row.source_notes = {**(row.source_notes or {}), **(notes or {})}
    chapters = list(row.source_chapters or [])
    changed = False
    for o in ordinals:
        if o not in chapters:
            chapters.append(o)
            changed = True
    if changed:
        row.source_chapters = sorted(chapters)
    db.flush()


# ── 一键提取(后台线程;可选章节范围,默认全书) ──
def _chapter_revisions(db, novel_id, from_chapter=None, to_chapter=None):
    stmt = select(Chapter.ordinal, Chapter.id, Chapter.updated_at).where(
        Chapter.novel_id == novel_id
    )
    if from_chapter is not None:
        stmt = stmt.where(Chapter.ordinal >= from_chapter)
    if to_chapter is not None:
        stmt = stmt.where(Chapter.ordinal <= to_chapter)
    return {
        str(n): f"{chapter_id}/{updated.isoformat()}"
        for n, chapter_id, updated in db.execute(stmt.order_by(Chapter.ordinal))
    }


def _completed_chapters(db, novel_id, revisions):
    done = set()
    records = db.scalars(
        select(SettingExtraction.chapter_progress).where(SettingExtraction.novel_id == novel_id)
    )
    for record in records:
        saved = (record or {}).get("revisions", {})
        done.update(
            n
            for n in (record or {}).get("done", [])
            if str(n) in revisions and saved.get(str(n)) == revisions[str(n)]
        )
    return sorted(done)


def start_extraction(
    db: Session,
    ctx: ProjectContext,
    novel_id: uuid.UUID,
    from_chapter: int | None = None,
    to_chapter: int | None = None,
    concurrency: int = 4,
) -> SettingExtraction:
    novel = db.get(Novel, novel_id)
    if novel is None or novel.project_id != ctx.project.id or novel.deleted_at is not None:
        raise NotFound("小说不存在")
    db.refresh(novel, with_for_update=True)
    running = db.scalar(
        select(SettingExtraction).where(
            SettingExtraction.novel_id == novel_id, SettingExtraction.status == "running"
        )
    )
    # 范围裁剪:按章节序号(ordinal,1 起),默认全书
    revisions = _chapter_revisions(db, novel_id, from_chapter, to_chapter)
    chapters = [int(n) for n in revisions]
    if not chapters:
        raise Conflict("所选范围内没有章节")
    if running:
        _recover_interrupted(db, running)
    if running and running.status == "running":
        if (running.chapter_progress or {}).get("requested") == chapters:
            return running
        raise Conflict("该小说已有提取任务在进行中")
    previous = latest_extraction(db, ctx.project.id, novel_id)
    done = _completed_chapters(db, novel_id, revisions)
    if (
        previous
        and previous.status in {"succeeded", "partial", "failed"}
        and (previous.chapter_progress or {}).get("requested") == chapters
        and previous.chapter_progress.get("revisions") == revisions
        and previous.chapter_progress.get("done") == done
    ):
        return previous
    job = SettingExtraction(
        project_id=ctx.project.id,
        novel_id=novel_id,
        status="succeeded" if done == chapters else "running",
        total_chapters=len(chapters),
        chapter_progress={"requested": chapters, "done": done, "failed": {}, "revisions": revisions},
        done_chapters=len(done),
        created_by=ctx.user.id,
    )
    db.add(job)
    db.flush()
    audit.record(
        db,
        action="setting.extract",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="novel",
        target_id=novel_id,
        detail={
            "job": str(job.id),
            "chapters": len(chapters),
            "from": from_chapter,
            "to": to_chapter,
        },
    )
    db.commit()  # 先落库,线程才能读到任务行
    if job.status == "succeeded":
        return job

    concurrency = max(1, min(int(concurrency or 1), 8))
    t = threading.Thread(
        target=_run_extraction, args=(job.id, from_chapter, to_chapter, concurrency), daemon=True
    )
    t.start()
    return job


def _worker_key(job_id):
    return int.from_bytes(job_id.bytes[:8], byteorder="big", signed=True)


def _run_extraction(job_id, from_chapter=None, to_chapter=None, concurrency=4):
    # Keep one connection for both the worker lease and the wave transactions.
    from app.core.database import SessionLocal

    with (
        SessionLocal() as factory,
        factory.get_bind().connect() as connection,
        Session(bind=connection, autoflush=False) as db,
    ):
        key = _worker_key(job_id)
        db.execute(select(func.pg_advisory_lock(key)))
        db.commit()
        try:
            _extract_waves(db, job_id, from_chapter, to_chapter, concurrency)
        finally:
            db.rollback()
            db.execute(select(func.pg_advisory_unlock(key)))
            db.commit()


def _extract_waves(db, job_id, from_chapter, to_chapter, concurrency):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        job = db.get(SettingExtraction, job_id)
        if job is None or job.status != "running":
            return
        strat = _strategy(db, job.project_id, None)  # 配了云 LLM 自动用,否则 mock
        progress = job.chapter_progress or {}
        revisions = progress.get("revisions") or _chapter_revisions(
            db, job.novel_id, from_chapter, to_chapter
        )
        requested = set(progress.get("requested", (int(n) for n in revisions)))
        done = set(progress.get("done", []))
        failed = dict(progress.get("failed", {}))
        remaining = sorted(requested - done)

        for wave_start in range(0, len(remaining), concurrency):
            db.refresh(job)
            if (job.chapter_progress or {}).get("stop_requested"):
                job.status = "cancelled"
                db.commit()
                return
            ordinals = remaining[wave_start : wave_start + concurrency]
            current = db.scalars(
                select(Chapter)
                .where(Chapter.novel_id == job.novel_id, Chapter.ordinal.in_(ordinals))
                .order_by(Chapter.ordinal)
            )
            wave = [
                ch
                for ch in current
                if revisions.get(str(ch.ordinal)) == f"{ch.id}/{ch.updated_at.isoformat()}"
            ]
            for ordinal in set(ordinals) - {ch.ordinal for ch in wave}:
                failed[str(ordinal)] = "本章已变更，请按当前章节重新提取"
            known = list_settings(db, job.project_id, novel_id=job.novel_id)

            def catalog(ordinal):
                return [
                    {"category": row.category, "name": row.name, "content": content}
                    for row in known
                    if (content := _known_content(row, ordinal))
                ]
            # 并发提取(工作线程只调 LLM,不碰数据库)
            results: list[tuple[int, list]] = []  # (章节序号, drafts)
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = {
                    ex.submit(strat.extract_settings, ch.content or "", catalog(ch.ordinal)): ch for ch in wave
                }
                for fut in as_completed(futures):
                    ch = futures[fut]
                    try:
                        drafts = fut.result()
                    except Exception as exc:  # noqa: BLE001  单章失败不终止,记录后继续
                        failed[str(ch.ordinal)] = exc.message if isinstance(exc, AppError) else "提取未完成，请重试此章"
                        continue
                    results.append((ch.ordinal, drafts))

            # 波内按 (category, name) 分组;多版本(含与已有设定并发更新)交给策略融合
            groups: dict[tuple[str, str], dict] = {}
            notes = {}
            chapter_by_ordinal = {c.ordinal: c for c in wave}
            for ordinal, drafts in results:
                for d in drafts:
                    key = (d.category, (d.name or "").strip())
                    if not key[1]:
                        continue
                    chapter = chapter_by_ordinal[ordinal]
                    note = {
                        "fact": d.chapter_fact or d.content,
                        "chapter_id": str(chapter.id),
                        "quote": d.quote if d.quote and d.quote in (chapter.content or "") else "",
                    }
                    notes.setdefault(key,{})[str(ordinal)] = note
                    g = groups.setdefault(key, {"versions": [], "ordinals": []})
                    g["versions"].append((ordinal, d.content))
                    g["ordinals"].append(ordinal)

            singles, conflicts = [], []
            for (cat, name), g in groups.items():
                versions = [c for _o, c in sorted(g["versions"])]
                if len(versions) == 1:
                    singles.append((cat, name, versions[0], g["ordinals"]))
                else:
                    conflicts.append(
                        {
                            "category": cat,
                            "name": name,
                            "versions": versions,
                            "ordinals": g["ordinals"],
                        }
                    )

            from app.adapters.contracts import SettingDraft

            for cat, name, content, ordinals in singles:
                _upsert_draft(
                    db,
                    job.project_id,
                    job.novel_id,
                    SettingDraft(category=cat, name=name, content=content),
                    ordinals,
                    job.created_by, notes[(cat,name)],
                )
            if conflicts:
                try:
                    merged = strat.merge_setting_versions(
                        [
                            {
                                "category": c["category"],
                                "name": c["name"],
                                "versions": c["versions"],
                            }
                            for c in conflicts
                        ]
                    )
                except Exception:
                    merged = []
                summaries = {(m.category, m.name): m for m in merged if m.content.strip()}
                for conflict in conflicts:
                    key = (conflict["category"], conflict["name"])
                    draft = summaries.get(key) or SettingDraft(
                        category=key[0],
                        name=key[1],
                        content="\n".join(dict.fromkeys(conflict["versions"])),
                    )
                    _upsert_draft(
                        db,
                        job.project_id,
                        job.novel_id,
                        draft,
                        conflict["ordinals"],
                        job.created_by,
                        notes[key],
                    )
            # The chapter receipt and its extracted settings must commit together.
            db.refresh(job, with_for_update=True)
            stopping = (job.chapter_progress or {}).get("stop_requested", False)
            for ordinal, _ in results:
                done.add(ordinal)
                failed.pop(str(ordinal), None)
            job.chapter_progress = {
                **progress,
                "revisions": revisions,
                "requested": sorted(requested),
                "done": sorted(done),
                "failed": failed,
            }
            job.done_chapters = len(done)
            job.error = f"{len(failed)} 章提取失败" if failed else None
            if stopping:
                job.status = "cancelled"
            db.commit()
            if stopping:
                return

        db.refresh(job, with_for_update=True)
        if job.status == "cancelled":
            return
        job.status = ("partial" if done else "failed") if failed else "succeeded"
        db.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
            job = db.get(SettingExtraction, job_id)
            if job is not None:
                job.status = "failed"
                job.error = exc.message if isinstance(exc, AppError) else "提取中断，已保留完成的章节"
                db.commit()
        except Exception:  # noqa: BLE001
            pass


def retry_extraction(db, ctx, job_id):
    job = get_extraction(db, ctx.project.id, job_id)
    db.scalar(select(Novel.id).where(Novel.id == job.novel_id).with_for_update())
    db.refresh(job, with_for_update=True)
    if job.status in {"running", "succeeded"}:
        return job
    if not (job.chapter_progress or {}).get("requested"):
        raise Conflict("旧任务没有逐章记录，请选择章节范围重新提取")
    active = db.scalar(
        select(SettingExtraction.id).where(
            SettingExtraction.novel_id == job.novel_id,
            SettingExtraction.status == "running",
            SettingExtraction.id != job.id,
        )
    )
    if active:
        raise Conflict("该小说已有提取任务在进行中")
    requested = job.chapter_progress["requested"]
    current = _chapter_revisions(db, job.novel_id, min(requested), max(requested))
    if job.chapter_progress.get("revisions") != current:
        raise Conflict("章节内容已变化，请按当前章节重新提取")
    done = _completed_chapters(db, job.novel_id, current)
    failed = {
        n: reason
        for n, reason in job.chapter_progress.get("failed", {}).items()
        if int(n) not in done
    }
    job.done_chapters = len(done)
    job.chapter_progress = {
        **job.chapter_progress,
        "done": done,
        "failed": failed,
        "stop_requested": False,
    }
    if done == requested:
        job.status, job.error = "succeeded", None
        db.flush()
        return job
    job.status, job.error = "running", None
    db.commit()
    threading.Thread(target=_run_extraction, args=(job.id,), daemon=True).start()
    return job


def get_extraction(db: Session, project_id: uuid.UUID, job_id: uuid.UUID) -> SettingExtraction:
    j = db.get(SettingExtraction, job_id)
    if j is None or j.project_id != project_id:
        raise NotFound("任务不存在")
    return _recover_interrupted(db, j)


def latest_extraction(
    db: Session, project_id: uuid.UUID, novel_id: uuid.UUID
) -> SettingExtraction | None:
    job = db.scalar(
        select(SettingExtraction)
        .where(SettingExtraction.project_id == project_id, SettingExtraction.novel_id == novel_id)
        .order_by(SettingExtraction.created_at.desc())
        .limit(1)
    )

    return _recover_interrupted(db, job)


def _recover_interrupted(db, job):
    if (
        job
        and job.status == "running"
        and job.updated_at < datetime.now(UTC) - timedelta(minutes=2)
    ):
        if db.scalar(select(func.pg_try_advisory_xact_lock(_worker_key(job.id)))):
            db.refresh(job, with_for_update=True)
            if job.status == "running" and job.updated_at < datetime.now(UTC) - timedelta(
                minutes=2
            ):
                job.status = "partial" if job.done_chapters else "failed"
                job.error = "提取已中断，可以继续未完成的章节"
                db.flush()
    return job


def cancel_extraction(db: Session, ctx: ProjectContext, job_id: uuid.UUID) -> SettingExtraction:
    j = get_extraction(db, ctx.project.id, job_id)
    db.refresh(j, with_for_update=True)
    if j.status == "running":
        j.chapter_progress = {**(j.chapter_progress or {}), "stop_requested":True}
        db.flush()
    return j
