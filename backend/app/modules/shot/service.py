"""shot 业务:CRUD、引用、状态机、看板、提示词 BOM 组合。"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.contracts import ConsistencyContext, consistency_registry
from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import Conflict, NotFound
from app.models.asset import Asset
from app.models.consistency import VisualIdentity
from app.models.enums import ProductionStatus
from app.models.narrative import Chapter, Novel, Scene
from app.models.prompt import PromptFragment
from app.models.shot import Shot, ShotAssetRef
from app.modules.shot import schemas

# 确保一致性策略注册
import app.adapters.consistency  # noqa: E402,F401

# 制作状态机:允许的流转
TRANSITIONS: dict[ProductionStatus, set[ProductionStatus]] = {
    ProductionStatus.to_design: {ProductionStatus.concept},
    ProductionStatus.concept: {ProductionStatus.generating},
    ProductionStatus.generating: {ProductionStatus.pending_review},
    ProductionStatus.pending_review: {ProductionStatus.approved, ProductionStatus.revising},
    ProductionStatus.revising: {ProductionStatus.generating},
    ProductionStatus.approved: {ProductionStatus.in_cut},
    ProductionStatus.in_cut: set(),
}


def _get(db: Session, project_id: uuid.UUID, shot_id: uuid.UUID) -> Shot:
    s = db.get(Shot, shot_id)
    if s is None or s.project_id != project_id or s.deleted_at is not None:
        raise NotFound("镜头不存在")
    return s


def list_shots(db: Session, project_id: uuid.UUID, scene_id: uuid.UUID) -> list[Shot]:
    return list(
        db.scalars(
            select(Shot).where(
                Shot.scene_id == scene_id, Shot.project_id == project_id, Shot.deleted_at.is_(None)
            ).order_by(Shot.ordinal)
        )
    )


def list_all_shots(
    db: Session,
    project_id: uuid.UUID,
    status: ProductionStatus | None = None,
    novel_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
) -> list[schemas.ShotOut]:
    """看板镜头列表:连带场次/章节/小说上下文,支持按小说、章节筛选。"""
    stmt = (
        select(
            Shot,
            Scene.code, Scene.title,
            Chapter.id, Chapter.ordinal, Chapter.title,
            Novel.id, Novel.title,
        )
        .join(Scene, Shot.scene_id == Scene.id)
        .outerjoin(Chapter, Scene.adapted_from_chapter_id == Chapter.id)
        .outerjoin(Novel, Chapter.novel_id == Novel.id)
        .where(Shot.project_id == project_id, Shot.deleted_at.is_(None))
    )
    if status is not None:
        stmt = stmt.where(Shot.production_status == status)
    if chapter_id is not None:
        stmt = stmt.where(Scene.adapted_from_chapter_id == chapter_id)
    if novel_id is not None:
        stmt = stmt.where(Chapter.novel_id == novel_id)

    out: list[schemas.ShotOut] = []
    for shot, sc_code, sc_title, ch_id, ch_ord, ch_title, nv_id, nv_title in db.execute(
        stmt.order_by(Shot.code)
    ):
        out.append(
            schemas.ShotOut(
                id=shot.id, code=shot.code, scene_id=shot.scene_id, ordinal=shot.ordinal,
                title=shot.title, description=shot.description,
                production_status=shot.production_status, prompt_override=shot.prompt_override,
                selected_generation_id=shot.selected_generation_id, created_at=shot.created_at,
                scene_code=sc_code, scene_title=sc_title,
                chapter_id=ch_id, chapter_ordinal=ch_ord, chapter_title=ch_title,
                novel_id=nv_id, novel_title=nv_title, storyboard=shot.storyboard or {},
            )
        )
    return out


def get(db: Session, project_id: uuid.UUID, shot_id: uuid.UUID) -> Shot:
    return _get(db, project_id, shot_id)


def update(db: Session, ctx: ProjectContext, shot_id: uuid.UUID, data: schemas.ShotUpdate) -> Shot:
    s = _get(db, ctx.project.id, shot_id)
    for f in ("title", "description", "prompt_override"):
        v = getattr(data, f)
        if v is not None:
            setattr(s, f, v)
    if data.storyboard is not None:
        # 按 key 合并;值为 None / "" 视为清除该字段
        merged = dict(s.storyboard or {})
        for k, v in data.storyboard.items():
            if v in (None, ""):
                merged.pop(k, None)
            else:
                merged[k] = v
        s.storyboard = merged
    db.flush()
    return s


def create_shot(db: Session, ctx: ProjectContext, scene_id: uuid.UUID, data: schemas.ShotCreate) -> Shot:
    """在某场景末尾新建一个镜头(分镜工作台手动加镜头)。"""
    from app.models.narrative import Scene
    from app.platform import coding

    scene = db.get(Scene, scene_id)
    if scene is None or scene.project_id != ctx.project.id or scene.deleted_at is not None:
        raise NotFound("场景不存在")
    last = db.scalar(
        select(func.max(Shot.ordinal)).where(Shot.scene_id == scene_id, Shot.deleted_at.is_(None))
    )
    ordinal = (last or 0) + 1
    shot = Shot(
        project_id=ctx.project.id, scene_id=scene_id, ordinal=ordinal,
        code=f"{scene.code}-SH{ordinal:03d}", title=data.title or f"镜头 {ordinal}",
        description=data.description, created_by=ctx.user.id, storyboard={},
    )
    db.add(shot)
    audit.record(db, action="shot.create", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type="shot", target_id=shot.id, detail={"scene_id": str(scene_id)})
    db.flush()
    return shot


def breakdown_scene(
    db: Session, ctx: ProjectContext, scene_id: uuid.UUID, strategy_name: str | None = None
) -> list[Shot]:
    """AI 拆分镜:把一个场景拆成多个带分镜规格的镜头,追加到该场景。"""
    from app.models.narrative import Chapter, Scene
    from app.modules.narrative.service import _strategy

    scene = db.get(Scene, scene_id)
    if scene is None or scene.project_id != ctx.project.id or scene.deleted_at is not None:
        raise NotFound("场景不存在")

    # 输入:场景文字 + 来源章节节选(场景正文多为 mock 留下的极短文本,故回溯章节剧情)
    parts: list[str] = []
    if scene.title:
        parts.append(f"场景:{scene.title}")
    if scene.summary:
        parts.append(f"概要:{scene.summary}")
    body = (scene.body or "").strip()
    if body and body != (scene.summary or "").strip():
        parts.append(f"场景正文:{body}")
    if scene.adapted_from_chapter_id:
        ch = db.get(Chapter, scene.adapted_from_chapter_id)
        if ch and ch.content:
            parts.append(f"所属章节背景(节选,供参考):{ch.content[:2500]}")
    text = "\n".join(parts) or (scene.title or "")

    strat = _strategy(db, ctx.project.id, strategy_name)
    suggestions = strat.suggest_storyboard_shots(text)
    if not suggestions:
        return list_shots(db, ctx.project.id, scene_id)

    # 替换模式:LLM 成功后才清空(软删)该场景现有镜头,再从头生成,避免"删了又没生成"
    cleared = 0
    for old in list_shots(db, ctx.project.id, scene_id):
        old.deleted_at = func.now()
        cleared += 1
    db.flush()

    for i, sug in enumerate(suggestions, 1):
        sb: dict = {}
        for k in ("shot_size", "camera_angle", "camera_move", "transition", "aspect_ratio", "pacing", "dialogue"):
            v = getattr(sug, k)
            if v:
                sb[k] = v
        if sug.duration_sec is not None:
            sb["duration_sec"] = sug.duration_sec
        db.add(Shot(
            project_id=ctx.project.id, scene_id=scene_id, ordinal=i,
            code=f"{scene.code}-SH{i:03d}", title=sug.title or f"镜头 {i}",
            description=sug.description, storyboard=sb, created_by=ctx.user.id,
        ))
    audit.record(db, action="shot.breakdown", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type="scene", target_id=scene_id, detail={"count": len(suggestions), "cleared": cleared})
    db.flush()
    return list_shots(db, ctx.project.id, scene_id)


def delete_shot(db: Session, ctx: ProjectContext, shot_id: uuid.UUID) -> None:
    s = _get(db, ctx.project.id, shot_id)
    s.deleted_at = func.now()
    audit.record(db, action="shot.delete", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type="shot", target_id=s.id)
    db.flush()


def reorder_shots(db: Session, ctx: ProjectContext, scene_id: uuid.UUID, shot_ids: list[uuid.UUID]) -> list[Shot]:
    """按给定顺序重排某场景的镜头 ordinal。"""
    shots = list_shots(db, ctx.project.id, scene_id)
    by_id = {s.id: s for s in shots}
    ordinal = 1
    for sid in shot_ids:
        s = by_id.get(sid)
        if s is not None:
            s.ordinal = ordinal
            ordinal += 1
    # 未在列表中的镜头排到后面,保持稳定
    for s in shots:
        if s.id not in set(shot_ids):
            s.ordinal = ordinal
            ordinal += 1
    db.flush()
    return list_shots(db, ctx.project.id, scene_id)


# ── 引用(浮动/钉死) ──
def set_asset_refs(db: Session, ctx: ProjectContext, shot_id: uuid.UUID, refs) -> list[ShotAssetRef]:
    s = _get(db, ctx.project.id, shot_id)
    db.query(ShotAssetRef).filter(ShotAssetRef.shot_id == s.id).delete()
    created = []
    for i, r in enumerate(refs):
        ref = ShotAssetRef(
            shot_id=s.id, asset_id=r.asset_id, role=r.role,
            ref_mode=r.ref_mode, pinned_version_id=r.pinned_version_id, ordinal=i,
        )
        db.add(ref)
        created.append(ref)
    db.flush()
    return created


def list_asset_refs(db: Session, project_id: uuid.UUID, shot_id: uuid.UUID) -> list[ShotAssetRef]:
    _get(db, project_id, shot_id)
    return list(
        db.scalars(
            select(ShotAssetRef).where(ShotAssetRef.shot_id == shot_id).order_by(ShotAssetRef.ordinal)
        )
    )


# ── 状态机 ──
def transition(db: Session, ctx: ProjectContext, shot_id: uuid.UUID, to: ProductionStatus) -> Shot:
    s = _get(db, ctx.project.id, shot_id)
    cur = s.production_status if isinstance(s.production_status, ProductionStatus) else ProductionStatus(s.production_status)
    if to not in TRANSITIONS.get(cur, set()):
        raise Conflict(
            f"非法状态流转:{cur.value} → {to.value}",
            {"from": cur.value, "allowed": [x.value for x in TRANSITIONS.get(cur, set())]},
        )
    s.production_status = to
    audit.record(db, action="shot.transition", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type="shot", target_id=s.id, detail={"from": cur.value, "to": to.value})
    db.flush()
    return s


# ── 进度看板 ──
def board(db: Session, project_id: uuid.UUID) -> schemas.BoardOut:
    rows = db.execute(
        select(Shot.production_status, func.count())
        .where(Shot.project_id == project_id, Shot.deleted_at.is_(None))
        .group_by(Shot.production_status)
    ).all()
    by_status = {(st.value if isinstance(st, ProductionStatus) else st): n for st, n in rows}
    # 补齐所有状态为 0
    full = {st.value: by_status.get(st.value, 0) for st in ProductionStatus}
    return schemas.BoardOut(total=sum(full.values()), by_status=full)


# ── 分镜规格 → 英文提示词术语 ──
STORYBOARD_PROMPT: dict[str, dict[str, str]] = {
    "shot_size": {
        "extreme_long": "extreme long shot", "long": "long shot", "full": "full shot",
        "medium": "medium shot", "medium_close": "medium close-up",
        "close_up": "close-up", "extreme_close_up": "extreme close-up",
    },
    "camera_angle": {
        "eye_level": "eye-level angle", "high": "high angle", "low": "low angle",
        "birds_eye": "bird's-eye view", "over_shoulder": "over-the-shoulder shot",
    },
    "camera_move": {
        "static": "static shot", "push_in": "camera push-in", "pull_out": "camera pull-out",
        "pan": "panning shot", "tilt": "tilting shot", "tracking": "tracking shot",
        "crane": "crane shot", "handheld": "handheld camera",
    },
}


def _storyboard_prompt_parts(sb: dict) -> list[str]:
    out: list[str] = []
    for key, mapping in STORYBOARD_PROMPT.items():
        val = sb.get(key)
        if val and val in mapping:
            out.append(mapping[val])
    ar = sb.get("aspect_ratio")
    if ar:
        out.append(f"{ar} aspect ratio")
    return out


# ── 提示词 BOM 组合 ──
def compose_prompt(db: Session, project_id: uuid.UUID, shot_id: uuid.UUID) -> schemas.ComposeOut:
    s = _get(db, project_id, shot_id)
    if s.prompt_override:
        return schemas.ComposeOut(final_prompt=s.prompt_override, parts=[s.prompt_override], overridden=True)

    parts: list[str] = []
    scene = db.get(Scene, s.scene_id)
    if scene and scene.summary:
        parts.append(scene.summary)

    refs = list_asset_refs(db, project_id, shot_id)
    for ref in refs:
        asset = db.get(Asset, ref.asset_id)
        if asset is None:
            continue
        vi = db.scalar(select(VisualIdentity).where(VisualIdentity.asset_id == asset.id))
        if vi is not None:
            frag_text = None
            if vi.prompt_fragment_id:
                frag = db.get(PromptFragment, vi.prompt_fragment_id)
                frag_text = frag.text if frag else None
            cctx = ConsistencyContext(
                asset_name=asset.name, prompt_fragment_text=frag_text,
                lora_ref=vi.lora_ref, reference_set=vi.reference_set,
            )
            strat = consistency_registry.create(vi.strategy)
            parts.extend(strat.prompt_parts(cctx))
        else:
            parts.append(asset.name)

    if s.description:
        parts.append(s.description)

    # 分镜规格 → 提示词术语(景别 / 机位 / 运镜 / 画面比例 自动拼入)
    parts.extend(_storyboard_prompt_parts(s.storyboard or {}))

    # 去重 + 去空:相同片段(忽略首尾空白/句末标点)不重复拼入
    seen: set[str] = set()
    deduped: list[str] = []
    for p in parts:
        if not p or not p.strip():
            continue
        key = p.strip().rstrip("。.!!??,,")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p.strip())
    return schemas.ComposeOut(final_prompt=", ".join(deduped), parts=deduped, overridden=False)
