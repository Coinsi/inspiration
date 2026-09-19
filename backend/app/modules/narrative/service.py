"""narrative 业务:小说导入(解析策略)、剧本/场次、AI 拆解与落库。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.contracts import SCRIPT_BLOCK_TYPES, decomposition_registry, parser_registry
from app.core import audit
from app.core.config import settings
from app.core.deps import ProjectContext
from app.core.errors import NotFound, Conflict, Locked
from app.models.enums import AssetType
from app.models.narrative import Chapter, Novel, Script, Scene
from app.models.shot import Shot
from app.modules.asset import schemas as asset_schemas
from app.modules.asset import service as asset_service
from app.modules.narrative import schemas
from app.platform import coding
from app.storage import cas

# 确保策略注册(导入触发)
import app.adapters.parser  # noqa: E402,F401
import app.adapters.decomposition  # noqa: E402,F401


# ── 小说导入 ──
def _pick_parser(filename: str, mime: str):
    for name in parser_registry.names():
        p = parser_registry.create(name)
        if p.can_handle(filename, mime):
            return p
    raise NotFound(f"无可用解析策略处理该文件:{filename}")


def import_novel(db: Session, ctx: ProjectContext, filename: str, mime: str, data: bytes) -> Novel:
    parser = _pick_parser(filename, mime)
    import io
    from pathlib import PurePosixPath

    doc = parser.parse(io.BytesIO(data))
    # 书名优先级:解析器提取的书名(如 Markdown 一级标题)> 上传文件名(去扩展名)> 兜底
    file_stem = PurePosixPath(filename.replace("\\", "/")).stem.strip()
    title = (doc.title or "").strip() or file_stem or "未命名小说"
    blob = cas.put_bytes(db, data, mime or "text/plain")
    novel = Novel(
        project_id=ctx.project.id,
        code=coding.next_code(db, ctx.project, "novel"),
        title=title,
        source_format=parser.name,
        original_blob_hash=blob.hash,
        created_by=ctx.user.id,
    )
    db.add(novel)
    db.flush()
    for ch in doc.chapters:
        db.add(Chapter(novel_id=novel.id, ordinal=ch.ordinal, title=ch.title, content=ch.content))
    audit.record(
        db,
        action="novel.import",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="novel",
        target_id=novel.id,
        detail={"chapters": len(doc.chapters)},
    )
    db.flush()
    return novel


def get_novel(db: Session, project_id: uuid.UUID, novel_id: uuid.UUID) -> Novel:
    n = db.get(Novel, novel_id)
    if n is None or n.project_id != project_id or n.deleted_at is not None:
        raise NotFound("小说不存在")
    return n


def rename_novel(db: Session, ctx: ProjectContext, novel_id: uuid.UUID, title: str) -> Novel:
    n = get_novel(db, ctx.project.id, novel_id)
    n.title = title.strip()
    audit.record(
        db,
        action="novel.rename",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="novel",
        target_id=n.id,
        detail={"title": n.title},
    )
    db.flush()
    return n


def delete_novel(db: Session, ctx: ProjectContext, novel_id: uuid.UUID) -> None:
    """软删除小说(标记 deleted_at);章节与已落库场次保持不动,可恢复。"""
    n = get_novel(db, ctx.project.id, novel_id)
    n.deleted_at = func.now()
    audit.record(
        db,
        action="novel.delete",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="novel",
        target_id=n.id,
    )
    db.flush()


def list_novels(db: Session, project_id: uuid.UUID) -> list[Novel]:
    return list(
        db.scalars(
            select(Novel)
            .where(Novel.project_id == project_id, Novel.deleted_at.is_(None))
            .order_by(Novel.created_at.desc())
        )
    )


def list_deleted_novels(db: Session, project_id: uuid.UUID) -> list[Novel]:
    """回收站:已软删除的小说。"""
    return list(
        db.scalars(
            select(Novel)
            .where(Novel.project_id == project_id, Novel.deleted_at.is_not(None))
            .order_by(Novel.deleted_at.desc())
        )
    )


def restore_novel(db: Session, ctx: ProjectContext, novel_id: uuid.UUID) -> Novel:
    n = db.get(Novel, novel_id)
    if n is None or n.project_id != ctx.project.id or n.deleted_at is None:
        raise NotFound("小说不存在或未删除")
    n.deleted_at = None
    audit.record(
        db,
        action="novel.restore",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="novel",
        target_id=n.id,
    )
    db.flush()
    return n


def chapters_of(db: Session, novel_id: uuid.UUID) -> list[Chapter]:
    return list(
        db.scalars(select(Chapter).where(Chapter.novel_id == novel_id).order_by(Chapter.ordinal))
    )


def _get_chapter(
    db: Session, project_id: uuid.UUID, chapter_id: uuid.UUID, *, allow_archived=False
) -> Chapter:
    query = (
        select(Chapter).join(Novel).where(Chapter.id == chapter_id, Novel.project_id == project_id)
    )
    if not allow_archived:
        query = query.where(Novel.deleted_at.is_(None))
    ch = db.scalar(query)
    if ch is None:
        raise NotFound("章节不存在")
    return ch


# ── 剧本 / 场次 ──
def create_script(db: Session, ctx: ProjectContext, title: str) -> Script:
    s = Script(
        project_id=ctx.project.id,
        code=coding.next_code(db, ctx.project, "script"),
        title=title,
        created_by=ctx.user.id,
    )
    db.add(s)
    db.flush()
    return s


def list_scripts(db: Session, project_id: uuid.UUID) -> list[Script]:
    return list(
        db.scalars(
            select(Script)
            .where(Script.project_id == project_id, Script.deleted_at.is_(None))
            .order_by(Script.created_at.desc())
        )
    )


def list_scenes(db: Session, project_id: uuid.UUID, script_id: uuid.UUID) -> list[Scene]:
    _get_script(db, project_id, script_id)
    return list(
        db.scalars(
            select(Scene)
            .where(
                Scene.script_id == script_id,
                Scene.project_id == project_id,
                Scene.deleted_at.is_(None),
            )
            .order_by(Scene.ordinal)
        )
    )


def list_all_scenes(
    db: Session,
    project_id: uuid.UUID,
    novel_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
) -> list[schemas.SceneListItem]:
    """分镜工作台:全项目场景 + 镜头数 + 章节/小说上下文,支持按小说/章节筛选。"""
    cnt = (
        select(Shot.scene_id.label("sid"), func.count().label("cnt"))
        .where(Shot.deleted_at.is_(None))
        .group_by(Shot.scene_id)
        .subquery()
    )
    stmt = (
        select(
            Scene,
            Chapter.id,
            Chapter.ordinal,
            Chapter.title,
            Novel.id,
            Novel.title,
            func.coalesce(cnt.c.cnt, 0),
        )
        .outerjoin(Chapter, Scene.adapted_from_chapter_id == Chapter.id)
        .outerjoin(Novel, Chapter.novel_id == Novel.id)
        .outerjoin(cnt, cnt.c.sid == Scene.id)
        .where(Scene.project_id == project_id, Scene.deleted_at.is_(None))
    )
    if chapter_id is not None:
        stmt = stmt.where(Scene.adapted_from_chapter_id == chapter_id)
    if novel_id is not None:
        stmt = stmt.where(Chapter.novel_id == novel_id)

    out: list[schemas.SceneListItem] = []
    for sc, ch_id, ch_ord, ch_title, nv_id, nv_title, shot_count in db.execute(
        stmt.order_by(Scene.code)
    ):
        out.append(
            schemas.SceneListItem(
                id=sc.id,
                code=sc.code,
                ordinal=sc.ordinal,
                title=sc.title,
                summary=sc.summary,
                shot_count=shot_count,
                chapter_id=ch_id,
                chapter_ordinal=ch_ord,
                chapter_title=ch_title,
                novel_id=nv_id,
                novel_title=nv_title,
            )
        )
    return out


def _get_script(db: Session, project_id: uuid.UUID, script_id: uuid.UUID) -> Script:
    s = db.get(Script, script_id)
    if s is None or s.project_id != project_id or s.deleted_at is not None:
        raise NotFound("剧本不存在")
    return s


def get_script(db: Session, project_id: uuid.UUID, script_id: uuid.UUID) -> Script:
    return _get_script(db, project_id, script_id)


def delete_script(db: Session, ctx: ProjectContext, script_id: uuid.UUID) -> None:
    """软删除剧本(标记 deleted_at);其下已落库场次/镜头保持不动。"""
    s = _get_script(db, ctx.project.id, script_id)
    s.deleted_at = func.now()
    audit.record(
        db,
        action="script.delete",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="script",
        target_id=s.id,
    )
    db.flush()


# ── 剧本正文(typed blocks)──
_BLOCK_TYPES = set(SCRIPT_BLOCK_TYPES)


def _normalize_blocks(blocks) -> list[dict]:
    """把 ScriptBlock / DTO / dict 统一规整为可入库的 dict,并补稳定 id。

    丢弃未知 block_type 与空文本块;text 去除首尾空白。
    """
    out: list[dict] = []
    for b in blocks or []:
        if isinstance(b, dict):
            btype = b.get("block_type") or b.get("type")
            text = b.get("text", "")
            bid = b.get("id")
        else:  # pydantic 模型(ScriptBlock / ScriptBlockDTO)
            btype = getattr(b, "block_type", None)
            text = getattr(b, "text", "")
            bid = getattr(b, "id", None)
        if btype not in _BLOCK_TYPES:
            continue
        text = (text or "").strip()
        if not text:
            continue
        out.append({"id": bid or uuid.uuid4().hex, "block_type": btype, "text": text})
    return out


def generate_script_from_chapter(
    db: Session, ctx: ProjectContext, data: schemas.GenerateScriptIn
) -> Script:
    """小说→剧本:用改编策略把章节改编成剧本正文,新建一条 Script 落库。

    S3:自动注入设定库上下文(该章出现的设定 + 全局世界观/体系),改编遵守既定人设。
    """
    ch = _get_chapter(db, ctx.project.id, data.chapter_id)
    strat = _strategy(db, ctx.project.id, data.strategy)
    from app.modules.setting.service import settings_context  # 局部导入避免循环依赖

    context = settings_context(db, ctx.project.id, ch.novel_id, ch.ordinal)
    blocks = _normalize_blocks(strat.adapt_chapter_to_script(ch.content, context))
    title = (data.title or ch.title or "未命名剧本").strip() or "未命名剧本"
    script = Script(
        project_id=ctx.project.id,
        code=coding.next_code(db, ctx.project, "script"),
        title=title,
        content_blocks=blocks,
        source_chapter_id=ch.id,
        created_by=ctx.user.id,
    )
    db.add(script)
    db.flush()
    audit.record(
        db,
        action="script.generate",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="script",
        target_id=script.id,
        detail={"chapter_id": str(ch.id), "blocks": len(blocks), "strategy": strat.name},
    )
    return script


def update_script_blocks(
    db: Session, ctx: ProjectContext, script_id: uuid.UUID, blocks, expected_revision=None
) -> Script:
    """整体保存剧本正文(P1:整篇替换;版本快照随 P3 接入)。"""
    script = _get_script(db, ctx.project.id, script_id)
    script = db.scalar(
        select(Script)
        .where(Script.id == script.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if script.status == "locked":
        raise Locked("剧本已锁定，请先解锁")
    if expected_revision is not None and script.content_revision != expected_revision:
        raise Conflict("剧本已被其他页面或助手修改。当前草稿已保留，请核对新版本后再保存。")
    script.content_blocks = _normalize_blocks(blocks)
    audit.record(
        db,
        action="script.update_blocks",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="script",
        target_id=script.id,
        detail={"blocks": len(script.content_blocks)},
    )
    db.flush()
    return script


def script_stats(db: Session, project_id: uuid.UUID, script_id: uuid.UUID) -> dict:
    """剧本数据(落库口径):本剧本派生的场次/分镜数 + 项目资产分类数。"""
    from app.models.asset import Asset

    script = _get_script(db, project_id, script_id)
    scenes = db.scalar(
        select(func.count())
        .select_from(Scene)
        .where(Scene.script_id == script.id, Scene.deleted_at.is_(None))
    )
    shots = db.scalar(
        select(func.count())
        .select_from(Shot)
        .join(Scene, Shot.scene_id == Scene.id)
        .where(Scene.script_id == script.id, Scene.deleted_at.is_(None), Shot.deleted_at.is_(None))
    )
    asset_rows = db.execute(
        select(Asset.type, func.count())
        .where(Asset.project_id == project_id, Asset.deleted_at.is_(None))
        .group_by(Asset.type)
    ).all()
    assets = {(t.value if hasattr(t, "value") else t): c for t, c in asset_rows}
    return {"scenes": scenes or 0, "shots": shots or 0, "assets": assets}


def export_script_fountain(db: Session, project_id: uuid.UUID, script_id: uuid.UUID) -> str:
    """把剧本正文导出为 Fountain(行业标准纯文本剧本格式)。"""
    script = _get_script(db, project_id, script_id)
    return _to_fountain(script.title, script.content_blocks or [])


def _group_blocks_into_scenes(blocks: list[dict]) -> list[tuple[str | None, str]]:
    """按 scene_heading 把剧本块分组为若干场景,返回 [(场景头文本, 该场正文)]。

    场景正文把角色/对白/动作等块渲染成可供下游(拆镜头/抽实体)阅读的文本。
    scene_heading 之前的零散块(若有)归入一个无标题的前导场景。
    """
    scenes: list[tuple[str | None, str]] = []
    cur_title: str | None = None
    cur_lines: list[str] = []

    def flush():
        if cur_title is not None or cur_lines:
            scenes.append((cur_title, "\n".join(cur_lines).strip()))

    for b in blocks or []:
        btype = b.get("block_type")
        txt = (b.get("text") or "").strip()
        if not txt:
            continue
        if btype == "scene_heading":
            flush()
            cur_title = txt
            cur_lines = []
        elif btype == "character":
            cur_lines.append(f"{txt}:")
        elif btype == "parenthetical":
            cur_lines.append(f"({txt.strip('()')})")
        else:  # action / dialogue / transition
            cur_lines.append(txt)
    flush()
    return scenes


def _script_to_plain_text(blocks: list[dict]) -> str:
    """把整篇剧本块拼成纯文本(供实体抽取)。"""
    parts = []
    for _title, body in _group_blocks_into_scenes(blocks):
        if _title:
            parts.append(_title)
        if body:
            parts.append(body)
    return "\n".join(parts).strip()


def decompose_script(
    db: Session, project_id: uuid.UUID, script_id: uuid.UUID, strategy_name: str | None
) -> schemas.DecomposeOut:
    """从剧本正文派生场次/镜头:按剧本自身的场景头切分(而非盲拆章节文本)。"""
    script = _get_script(db, project_id, script_id)
    strat = _strategy(db, project_id, strategy_name)
    src_ord = None
    if script.source_chapter_id:
        ch = db.get(Chapter, script.source_chapter_id)
        src_ord = ch.ordinal if ch else None
    grouped = _group_blocks_into_scenes(script.content_blocks or [])
    scenes = []
    for i, (title, body) in enumerate(grouped, 1):
        shots = strat.suggest_shots(body or title or "")
        scenes.append(
            schemas.SceneSuggestionOut(
                title=title or f"场景 {i}",
                summary=(body[:30].replace("\n", " ") if body else ""),
                body=body,
                source_chapter_ordinal=src_ord,
                shots=[schemas.ShotBrief(title=s.title, description=s.description) for s in shots],
            )
        )
    return schemas.DecomposeOut(strategy=strat.name, scenes=scenes)


def _to_fountain(title: str, blocks: list[dict]) -> str:
    lines: list[str] = [f"Title: {title}", ""]
    for b in blocks:
        btype = b.get("block_type")
        text = (b.get("text") or "").strip()
        if not text:
            continue
        if btype == "scene_heading":
            lines += ["", text.upper()]
        elif btype == "action":
            lines += ["", text]
        elif btype == "character":
            lines += ["", text.upper()]
        elif btype == "parenthetical":
            inner = text.strip("()")
            lines.append(f"({inner})")
        elif btype == "dialogue":
            lines.append(text)
        elif btype == "transition":
            lines += ["", text.upper()]
    return "\n".join(lines).strip() + "\n"


# ── AI 拆解 ──
def _llm_config(db: Session, project_id: uuid.UUID):
    """取项目内已启用的 LLM 供应商配置(provider_config kind=llm)。"""
    from app.modules.generation.channels import llm_config
    return llm_config(db, project_id)


def _strategy(db: Session, project_id: uuid.UUID, name: str | None):
    if name == "mock":
        return decomposition_registry.create("mock")
    pc = _llm_config(db, project_id)
    # 未显式指定:配了 LLM 则自动用云模型,否则回落默认(mock)
    chosen = name or ("cloud_llm" if pc is not None else settings.decomposition_strategy)
    kwargs = {}
    if chosen == "cloud_llm" and pc is not None:
        from app.core.crypto import decrypt

        kwargs = {
            "base_url": pc.endpoint,
            "api_key": decrypt(pc.credentials_encrypted),
            "model": (pc.config or {}).get("model"),
        }
    return decomposition_registry.create(chosen, **kwargs)


def decompose_chapter(
    db: Session, project_id: uuid.UUID, chapter_id: uuid.UUID, strategy_name: str | None
) -> schemas.DecomposeOut:
    ch = _get_chapter(db, project_id, chapter_id)
    strat = _strategy(db, project_id, strategy_name)
    scenes = []
    for sc in strat.suggest_scenes(ch.content):
        shots = strat.suggest_shots(sc.body or sc.summary)
        scenes.append(
            schemas.SceneSuggestionOut(
                title=sc.title,
                summary=sc.summary,
                body=sc.body,
                source_chapter_ordinal=ch.ordinal,
                shots=[schemas.ShotBrief(title=s.title, description=s.description) for s in shots],
            )
        )
    return schemas.DecomposeOut(strategy=strat.name, scenes=scenes)


def apply_scenes(
    db: Session, ctx: ProjectContext, script_id: uuid.UUID, data: schemas.ApplyScenesIn
) -> list[Scene]:
    script = _get_script(db, ctx.project.id, script_id)
    chapter_id = data.chapter_id or script.source_chapter_id
    if chapter_id:
        _get_chapter(db, ctx.project.id, chapter_id, allow_archived=data.chapter_id is None)
    base_ord = db.scalar(
        select(func.coalesce(func.max(Scene.ordinal), 0)).where(Scene.script_id == script.id)
    )
    created = []
    for i, sc in enumerate(data.scenes, 1):
        scene = Scene(
            project_id=ctx.project.id,
            script_id=script.id,
            code=coding.next_code(db, ctx.project, "scene"),
            ordinal=base_ord + i,
            title=sc.title,
            summary=sc.summary,
            body=sc.body,
            adapted_from_chapter_id=chapter_id,
            created_by=ctx.user.id,
        )
        db.add(scene)
        db.flush()
        for j, shot in enumerate(sc.shots, 1):
            db.add(
                Shot(
                    project_id=ctx.project.id,
                    scene_id=scene.id,
                    code=f"{scene.code}-SH{j:03d}",
                    ordinal=j,
                    title=shot.title,
                    description=shot.description,
                    created_by=ctx.user.id,
                )
            )
        created.append(scene)
    audit.record(
        db,
        action="narrative.apply_scenes",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="script",
        target_id=script.id,
        detail={"scenes": len(created)},
    )
    db.flush()
    return created


def extract_entities(
    db: Session, project_id: uuid.UUID, data: schemas.ExtractEntitiesIn, strategy_name: str | None
) -> list[schemas.EntityDraftOut]:
    text = data.text
    if text is None and data.script_id:
        text = _script_to_plain_text(
            _get_script(db, project_id, data.script_id).content_blocks or []
        )
    if text is None and data.chapter_id:
        text = _get_chapter(db, project_id, data.chapter_id).content
    if not text:
        raise NotFound("需提供 text、script_id 或 chapter_id")
    drafts = _strategy(db, project_id, strategy_name).extract_entities(text)
    return [schemas.EntityDraftOut(type=d.type, name=d.name, summary=d.summary) for d in drafts]


def apply_entities(db: Session, ctx: ProjectContext, data: schemas.ApplyEntitiesIn) -> list:
    created = []
    for e in data.entities:
        a = asset_service.create(
            db, ctx, asset_schemas.AssetIn(type=AssetType(e.type), name=e.name, summary=e.summary)
        )
        created.append(a)
    return created
