"""setting 业务:设定 CRUD + 一键全书后台提取(逐章增量合并)+ 转资产。

全书提取在**后台线程**中跑(本地 CELERY_EAGER 环境同样可用):
每章调用拆解策略 extract_settings(章节正文, 已有设定名录) → 同名条目整条覆盖(AI 给出
合并后的完整版),新名字新建;每章提交一次,进度可轮询、可取消、中断后重发即续跑
(已有名录会让 AI 不再重复产出已完整的设定)。
"""
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import Conflict, NotFound
from app.models.enums import AssetType
from app.models.narrative import Chapter, Novel
from app.models.setting import Setting, SettingExtraction
from app.modules.asset import schemas as asset_schemas
from app.modules.asset import service as asset_service
from app.modules.narrative.service import _strategy
from app.modules.setting import schemas

# 设定分类 → 资产类型(可转资产的三类)
_CATEGORY_TO_ASSET = {"character": AssetType.character, "location": AssetType.location, "item": AssetType.prop}


# ── CRUD ──
def list_settings(
    db: Session, project_id: uuid.UUID,
    novel_id: uuid.UUID | None = None, category: str | None = None,
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
    s = Setting(
        project_id=ctx.project.id, novel_id=data.novel_id,
        category=data.category, name=data.name.strip(), content=data.content,
        created_by=ctx.user.id,
    )
    db.add(s)
    db.flush()
    return s


def update_setting(db: Session, ctx: ProjectContext, setting_id: uuid.UUID, data: schemas.SettingUpdate) -> Setting:
    s = _get(db, ctx.project.id, setting_id)
    if data.category is not None:
        s.category = data.category
    if data.name is not None:
        s.name = data.name.strip()
    if data.content is not None:
        s.content = data.content
    db.flush()
    return s


def delete_setting(db: Session, ctx: ProjectContext, setting_id: uuid.UUID) -> None:
    s = _get(db, ctx.project.id, setting_id)
    db.delete(s)
    db.flush()


def to_asset(db: Session, ctx: ProjectContext, setting_id: uuid.UUID):
    """人物/地点/物品类设定 → 落为资产(接通既有资产链路)。"""
    s = _get(db, ctx.project.id, setting_id)
    asset_type = _CATEGORY_TO_ASSET.get(s.category)
    if asset_type is None:
        raise Conflict(f"该分类({s.category})不支持转为资产(支持:{','.join(_CATEGORY_TO_ASSET)})")
    a = asset_service.create(
        db, ctx, asset_schemas.AssetIn(type=asset_type, name=s.name, summary=s.content[:2000])
    )
    audit.record(db, action="setting.to_asset", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type="setting", target_id=s.id, detail={"asset_id": str(a.id)})
    return a


# ── 设定上下文(S3:供剧本生成/AI 助手注入) ──
_GLOBAL_CATEGORIES = ("world", "power_system")  # 全局规则类:无论哪章都注入

_CATEGORY_LABEL = {
    "world": "世界观", "power_system": "力量体系", "faction": "势力组织", "character": "人物",
    "location": "地点", "item": "物品道具", "glossary": "术语", "timeline": "大事记",
}


def settings_context(
    db: Session, project_id: uuid.UUID, novel_id: uuid.UUID,
    chapter_ordinal: int | None = None, max_chars: int = 3500,
) -> str:
    """组装某章相关的设定上下文文本:出现在该章的设定 + 全局规则类设定。

    chapter_ordinal 为空时返回全书设定摘要。按分类分组,单条与总量均截断。
    """
    rows = list_settings(db, project_id, novel_id=novel_id)
    picked = [
        s for s in rows
        if s.category in _GLOBAL_CATEGORIES
        or chapter_ordinal is None
        or chapter_ordinal in (s.source_chapters or [])
    ]
    if not picked:
        return ""
    by_cat: dict[str, list] = {}
    for s in picked:
        by_cat.setdefault(s.category, []).append(s)
    parts: list[str] = []
    total = 0
    for cat, items in by_cat.items():
        label = _CATEGORY_LABEL.get(cat, cat)
        for s in items:
            line = f"【{label}】{s.name}:{(s.content or '').strip()[:240]}"
            if total + len(line) > max_chars:
                return "\n".join(parts)
            parts.append(line)
            total += len(line)
    return "\n".join(parts)


# ── 设定合并 upsert(提取线程使用;ordinals 为该条设定本次涉及的章节序号) ──
def _upsert_draft(db: Session, project_id: uuid.UUID, novel_id: uuid.UUID,
                  draft, ordinals: list[int], user_id: uuid.UUID | None) -> None:
    name = (draft.name or "").strip()
    if not name:
        return
    row = db.scalar(
        select(Setting).where(
            Setting.project_id == project_id, Setting.novel_id == novel_id,
            Setting.category == draft.category, Setting.name == name,
        )
    )
    if row is None:
        row = Setting(project_id=project_id, novel_id=novel_id, category=draft.category,
                      name=name, content=draft.content, created_by=user_id)
        db.add(row)
    else:
        row.content = draft.content  # AI 已给出合并后的完整版,整条覆盖
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
def start_extraction(
    db: Session, ctx: ProjectContext, novel_id: uuid.UUID,
    from_chapter: int | None = None, to_chapter: int | None = None,
    concurrency: int = 4,
) -> SettingExtraction:
    novel = db.get(Novel, novel_id)
    if novel is None or novel.project_id != ctx.project.id or novel.deleted_at is not None:
        raise NotFound("小说不存在")
    running = db.scalar(
        select(SettingExtraction).where(
            SettingExtraction.novel_id == novel_id, SettingExtraction.status == "running"
        )
    )
    if running is not None:
        raise Conflict("该小说已有提取任务在进行中")
    # 范围裁剪:按章节序号(ordinal,1 起),默认全书
    stmt = select(Chapter.id).where(Chapter.novel_id == novel_id)
    if from_chapter is not None:
        stmt = stmt.where(Chapter.ordinal >= from_chapter)
    if to_chapter is not None:
        stmt = stmt.where(Chapter.ordinal <= to_chapter)
    chapters = list(db.scalars(stmt))
    if not chapters:
        raise Conflict("所选范围内没有章节")
    job = SettingExtraction(
        project_id=ctx.project.id, novel_id=novel_id,
        status="running", total_chapters=len(chapters), done_chapters=0, created_by=ctx.user.id,
    )
    db.add(job)
    db.flush()
    audit.record(db, action="setting.extract", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type="novel", target_id=novel_id,
                 detail={"job": str(job.id), "chapters": len(chapters),
                         "from": from_chapter, "to": to_chapter})
    db.commit()  # 先落库,线程才能读到任务行

    concurrency = max(1, min(int(concurrency or 1), 8))
    t = threading.Thread(target=_run_extraction, args=(job.id, from_chapter, to_chapter, concurrency), daemon=True)
    t.start()
    return job


def _run_extraction(
    job_id: uuid.UUID, from_chapter: int | None = None, to_chapter: int | None = None, concurrency: int = 4
) -> None:
    """后台线程:波次并发提取 —— 每波并发 N 章(同一份已有名录快照),
    波内同名冲突交给策略 merge_setting_versions 融合,波结束统一落库;
    下一波拿到合并后的最新名录,保持"逐步补全"语义。进度逐章提交,随时可取消。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        job = db.get(SettingExtraction, job_id)
        if job is None:
            return
        strat = _strategy(db, job.project_id, None)  # 配了云 LLM 自动用,否则 mock
        stmt = (
            select(Chapter).join(Novel, Chapter.novel_id == Novel.id)
            .where(Chapter.novel_id == job.novel_id)
        )
        if from_chapter is not None:
            stmt = stmt.where(Chapter.ordinal >= from_chapter)
        if to_chapter is not None:
            stmt = stmt.where(Chapter.ordinal <= to_chapter)
        chapters = list(db.scalars(stmt.order_by(Chapter.ordinal)))

        for wave_start in range(0, len(chapters), concurrency):
            db.refresh(job)
            if job.status == "cancelled":
                db.commit()
                return
            wave = chapters[wave_start: wave_start + concurrency]
            existing = [
                {"category": s.category, "name": s.name, "content": s.content}
                for s in list_settings(db, job.project_id, novel_id=job.novel_id)
            ]

            # 并发提取(工作线程只调 LLM,不碰数据库)
            results: list[tuple[int, list]] = []  # (章节序号, drafts)
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = {ex.submit(strat.extract_settings, ch.content or "", existing): ch for ch in wave}
                for fut in as_completed(futures):
                    ch = futures[fut]
                    try:
                        drafts = fut.result()
                    except Exception as exc:  # noqa: BLE001  单章失败不终止,记录后继续
                        job.error = f"第 {ch.ordinal} 章提取失败:{exc}"
                        drafts = []
                    results.append((ch.ordinal, drafts))
                    job.done_chapters += 1
                    db.commit()  # 进度逐章可见

            # 波内按 (category, name) 分组;多版本(含与已有设定并发更新)交给策略融合
            groups: dict[tuple[str, str], dict] = {}
            for ordinal, drafts in results:
                for d in drafts:
                    key = (d.category, (d.name or "").strip())
                    if not key[1]:
                        continue
                    g = groups.setdefault(key, {"versions": [], "ordinals": []})
                    g["versions"].append((ordinal, d.content))
                    g["ordinals"].append(ordinal)

            singles, conflicts = [], []
            for (cat, name), g in groups.items():
                versions = [c for _o, c in sorted(g["versions"])]
                if len(versions) == 1:
                    singles.append((cat, name, versions[0], g["ordinals"]))
                else:
                    conflicts.append({"category": cat, "name": name, "versions": versions, "ordinals": g["ordinals"]})

            from app.adapters.contracts import SettingDraft

            for cat, name, content, ordinals in singles:
                _upsert_draft(db, job.project_id, job.novel_id,
                              SettingDraft(category=cat, name=name, content=content), ordinals, job.created_by)
            if conflicts:
                try:
                    merged = strat.merge_setting_versions(
                        [{"category": c["category"], "name": c["name"], "versions": c["versions"]} for c in conflicts]
                    )
                except Exception:  # noqa: BLE001  合并失败退化为取最长版本
                    merged = [
                        SettingDraft(category=c["category"], name=c["name"], content=max(c["versions"], key=len))
                        for c in conflicts
                    ]
                ordinal_map = {(c["category"], c["name"]): c["ordinals"] for c in conflicts}
                for m in merged:
                    _upsert_draft(db, job.project_id, job.novel_id, m,
                                  ordinal_map.get((m.category, m.name), []), job.created_by)
            db.commit()

        job.status = "succeeded"
        db.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            job = db.get(SettingExtraction, job_id)
            if job is not None:
                job.status = "failed"
                job.error = str(exc)
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def get_extraction(db: Session, project_id: uuid.UUID, job_id: uuid.UUID) -> SettingExtraction:
    j = db.get(SettingExtraction, job_id)
    if j is None or j.project_id != project_id:
        raise NotFound("任务不存在")
    return j


def latest_extraction(db: Session, project_id: uuid.UUID, novel_id: uuid.UUID) -> SettingExtraction | None:
    return db.scalar(
        select(SettingExtraction)
        .where(SettingExtraction.project_id == project_id, SettingExtraction.novel_id == novel_id)
        .order_by(SettingExtraction.created_at.desc())
        .limit(1)
    )


def cancel_extraction(db: Session, ctx: ProjectContext, job_id: uuid.UUID) -> SettingExtraction:
    j = get_extraction(db, ctx.project.id, job_id)
    if j.status == "running":
        j.status = "cancelled"
        db.flush()
    return j
