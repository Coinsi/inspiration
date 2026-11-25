"""shot 业务:CRUD、引用、状态机、看板、提示词 BOM 组合。"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import Conflict, NotFound
from app.models.asset import Asset
from app.models.enums import ProductionStatus
from app.models.narrative import Chapter, Novel, Scene
from app.models.shot import Shot, ShotAssetRef
from app.modules.shot import breakdown, schemas

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


def _get(db: Session, project_id: uuid.UUID, shot_id: uuid.UUID, *, lock=False) -> Shot:
    query = select(Shot).where(
        Shot.id == shot_id, Shot.project_id == project_id, Shot.deleted_at.is_(None)
    )
    s = db.scalar(query.with_for_update() if lock else query)
    if s is None:
        raise NotFound("镜头不存在")
    return s


def _scene(db: Session, project_id: uuid.UUID, scene_id: uuid.UUID, *, lock=False) -> Scene:
    query = select(Scene).where(
        Scene.id == scene_id, Scene.project_id == project_id, Scene.deleted_at.is_(None)
    )
    scene = db.scalar(query.with_for_update() if lock else query)
    if scene is None:
        raise NotFound("场景不存在")
    return scene


def list_shots(db: Session, project_id: uuid.UUID, scene_id: uuid.UUID, *, removed=False) -> list[Shot]:
    _scene(db, project_id, scene_id)
    query = select(Shot).where(Shot.scene_id == scene_id, Shot.project_id == project_id)
    if removed:
        query = query.where(Shot.deleted_at.is_not(None)).order_by(Shot.deleted_at.desc())
    else:
        query = query.where(Shot.deleted_at.is_(None))
    return list(db.scalars(query.order_by(Shot.ordinal, Shot.created_at, Shot.id)))


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
    s = _get(db, ctx.project.id, shot_id, lock=True)
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
    scene = _scene(db, ctx.project.id, scene_id, lock=True)
    ordinal, code_number = breakdown.next_scene_shot_numbers(db, scene)
    shot = Shot(
        project_id=ctx.project.id, scene_id=scene_id, ordinal=ordinal,
        code=f"{scene.code}-SH{code_number:03d}", title=data.title or f"镜头 {ordinal}",
        description=data.description, created_by=ctx.user.id, storyboard={},
    )
    db.add(shot)
    db.flush()
    audit.record(db, action="shot.create", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type="shot", target_id=shot.id, detail={"scene_id": str(scene_id)})
    db.flush()
    return shot


def breakdown_scene(
    db: Session, ctx: ProjectContext, scene_id: uuid.UUID, strategy_name: str | None = None, *, checkpoint=None,
) -> list[Shot]:
    """Create a sequence, or enrich existing shots without replacing their identity."""
    from app.modules.narrative.service import _strategy
    from app.modules.asset.story_sources import AssetNames, scope_for
    scene = _scene(db, ctx.project.id, scene_id, lock=True)
    existing = list_shots(db, ctx.project.id, scene_id)
    missing = [
        s
        for s in existing
        if not all((s.storyboard or {}).get(k) for k in ("still_prompt", "motion_prompt"))
    ]
    if existing and not missing:
        return existing
    text = "\n".join(dict.fromkeys(p for p in (scene.title, scene.summary, scene.body) if p))
    strategy = _strategy(db, ctx.project.id, strategy_name)
    if existing:
        names = AssetNames(
            db, ctx.project.id, scope_for(db, scene.script_id, scene.adapted_from_chapter_id)
        )
        completed = len(existing) - len(missing)
        failed = 0
        positions = {shot.id: i for i, shot in enumerate(existing, 1)}
        for shot in missing:
            if checkpoint:
                checkpoint(len(existing), completed, failed, positions[shot.id], shot.title)
            try:
                with db.begin_nested():
                    original_text = (shot.title, shot.description)
                    if strategy.name == "mock":
                        from app.adapters.contracts import StoryboardShotSuggestion

                        detail = shot.description or shot.title or scene.title or "空镜头"
                        draft = StoryboardShotSuggestion(
                            title=shot.title or "",
                            description=detail,
                            still_prompt=detail,
                            motion_prompt=detail,
                        )
                    else:
                        instruction = f"本场背景：{text[:1800]}。只细化已有的一镜，恰好返回一个镜头。镜头标题：{shot.title}。镜头内容：{shot.description}"
                        drafts = breakdown.suggest(
                            db, strategy, ctx.project.id, instruction, scene.adapted_from_chapter_id
                        )
                        if len(drafts) != 1 or not drafts[0].still_prompt.strip():
                            raise Conflict("模型未返回该镜头的定格画面，请重试此镜")
                        draft = drafts[0]
                    db.refresh(shot, with_for_update=True)
                    if shot.deleted_at:
                        continue
                    if original_text != (shot.title, shot.description):
                        raise Conflict("镜头内容已更新，可以按最新内容补全分镜")
                    breakdown.save_draft(db, shot, draft, names)
                    shot.storyboard = {
                        k: v for k, v in shot.storyboard.items() if k != "breakdown_error"
                    }
                completed += 1
            except Exception as exc:
                from app.core.errors import AppError

                shot.storyboard = {
                    **(shot.storyboard or {}),
                    "breakdown_error": exc.message
                    if isinstance(exc, AppError)
                    else "未取得该镜的分镜，请重试",
                }
                failed += 1
    else:
        drafts = breakdown.suggest(
            db, strategy, ctx.project.id, text, scene.adapted_from_chapter_id
        )
        if not drafts:
            raise Conflict("模型没有返回可用的镜头，请重试")
        breakdown.create_drafts(db, ctx, scene, drafts)
    db.flush()
    audit.record(db, action="shot.breakdown", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type="scene", target_id=scene.id, detail={"count": len(existing) if existing else len(drafts)})
    return list_shots(db, ctx.project.id, scene_id)


def _selected_shots(db: Session, scene: Scene, shot_ids: list[uuid.UUID]) -> list[Shot]:
    ids = set(shot_ids)
    shots = list(db.scalars(
        select(Shot).where(
            Shot.scene_id == scene.id, Shot.project_id == scene.project_id, Shot.id.in_(ids)
        ).order_by(Shot.ordinal, Shot.created_at, Shot.id).with_for_update()
    ))
    if len(shots) != len(ids):
        raise NotFound("所选镜头不在本场景中，请刷新")
    return shots


def remove_shots(
    db: Session, ctx: ProjectContext, scene_id: uuid.UUID, shot_ids: list[uuid.UUID]
) -> int:
    scene = _scene(db, ctx.project.id, scene_id, lock=True)
    shots = [s for s in _selected_shots(db, scene, shot_ids) if s.deleted_at is None]
    removed_at = datetime.now(UTC)
    for shot in shots:
        shot.deleted_at = removed_at
        audit.record(db, action="shot.delete", user_id=ctx.user.id, project_id=ctx.project.id,
                     target_type="shot", target_id=shot.id, detail={"scene_id": str(scene.id)})
    db.flush()
    return len(shots)


def restore_shots(
    db: Session, ctx: ProjectContext, scene_id: uuid.UUID, shot_ids: list[uuid.UUID]
) -> int:
    scene = _scene(db, ctx.project.id, scene_id, lock=True)
    shots = [s for s in _selected_shots(db, scene, shot_ids) if s.deleted_at is not None]
    if not shots:
        return 0
    history = db.execute(
        select(Shot.ordinal, Shot.code, Shot.deleted_at).where(
            Shot.project_id == scene.project_id, Shot.scene_id == scene.id
        )
    ).all()
    occupied_ordinals = {r.ordinal for r in history if r.deleted_at is None}
    occupied_codes = {r.code for r in history if r.deleted_at is None}
    next_ordinal, next_code = breakdown.next_shot_numbers(
        ((r.ordinal, r.code) for r in history), scene.code
    )
    for shot in shots:
        # Older records can share a position or code with a current shot.
        # Keep available originals; only append/renumber a genuine collision.
        if shot.ordinal in occupied_ordinals:
            shot.ordinal = next_ordinal
            next_ordinal += 1
        if shot.code in occupied_codes:
            shot.code = f"{scene.code}-SH{next_code:03d}"
            next_code += 1
        occupied_ordinals.add(shot.ordinal)
        occupied_codes.add(shot.code)
        shot.deleted_at = None
        audit.record(db, action="shot.restore", user_id=ctx.user.id, project_id=ctx.project.id,
                     target_type="shot", target_id=shot.id, detail={"scene_id": str(scene.id)})
    db.flush()
    return len(shots)


def delete_shot(db: Session, ctx: ProjectContext, shot_id: uuid.UUID) -> None:
    scene_id = db.scalar(
        select(Shot.scene_id).where(Shot.id == shot_id, Shot.project_id == ctx.project.id)
    )
    if scene_id is None:
        raise NotFound("镜头不存在")
    remove_shots(db, ctx, scene_id, [shot_id])


def reorder_shots(db: Session, ctx: ProjectContext, scene_id: uuid.UUID, shot_ids: list[uuid.UUID]) -> list[Shot]:
    """按给定顺序重排某场景的镜头 ordinal。"""
    _scene(db, ctx.project.id, scene_id, lock=True)
    shots = list_shots(db, ctx.project.id, scene_id)
    by_id = {s.id: s for s in shots}
    if not set(shot_ids) <= by_id.keys():
        raise NotFound("所选镜头不在当前分镜中，请刷新")
    ordered = dict.fromkeys([*shot_ids, *by_id])
    for ordinal, sid in enumerate(ordered, 1):
        by_id[sid].ordinal = ordinal
    db.flush()
    return list_shots(db, ctx.project.id, scene_id)


# ── 引用(浮动/钉死) ──
def set_asset_refs(db: Session, ctx: ProjectContext, shot_id: uuid.UUID, refs) -> list[schemas.AssetRefOut]:
    from app.modules.shot.inputs import pinned
    s = _get(db, ctx.project.id, shot_id, lock=True)
    previous = {
        (r.asset_id, r.role): r
        for r in db.scalars(select(ShotAssetRef).where(ShotAssetRef.shot_id == s.id))
    }
    requested = {(r.asset_id, r.role) for r in refs}
    if len(requested) != len(refs):
        raise Conflict("同一项资产已在镜头中")
    board = s.storyboard or {}
    pending = list(board.get("unresolved_subjects") or [])
    resolved_usage = {}
    retained = set()
    for r in refs:
        key = (r.asset_id, r.role)
        asset = db.get(Asset, r.asset_id)
        if not asset or asset.project_id != ctx.project.id:
            raise NotFound("参考的资产不存在")
        if asset.deleted_at:
            prior = previous.get(key)
            if prior is None:
                raise NotFound("参考的资产已移入回收站")
            original = schemas.AssetUsage.model_validate(prior.usage or {})
            if (
                r.resolves_subject or r.ref_mode != prior.ref_mode
                or r.pinned_version_id != prior.pinned_version_id
                or (r.usage is not None and r.usage != original)
            ):
                raise Conflict("请先恢复资产，再修改这条引用")
            retained.add(key)
            continue
        if r.resolves_subject:
            subject = next(
                (
                    x
                    for x in pending
                    if x.get("name") == r.resolves_subject and x.get("type") == asset.type
                ),
                None,
            )
            if subject is None:
                raise Conflict("待确认的镜头主体已变化，请刷新")
            pending.remove(subject)
            resolved_usage[(r.asset_id, r.role)] = {
                k: subject.get(k) for k in ("look", "evidence", "aspects") if subject.get(k)
            }
        if r.ref_mode == "pinned":
            pinned(db, ctx.project.id, asset.id, r.pinned_version_id)
    for key in previous.keys() - requested:
        db.delete(previous[key])
    added_ids = {str(r.asset_id) for r in refs}
    suppressed = (
        set(board.get("suppressed_asset_ids", [])) | {str(aid) for aid, _ in previous}
    ) - added_ids
    if pending != board.get("unresolved_subjects", []) or suppressed != set(
        board.get("suppressed_asset_ids", [])
    ):
        s.storyboard = {**board, "unresolved_subjects": pending, "suppressed_asset_ids": sorted(suppressed)}
    for i, r in enumerate(refs):
        key = (r.asset_id, r.role)
        ref = previous.get(key)
        if ref is None:
            ref = ShotAssetRef(shot_id=s.id, asset_id=r.asset_id, role=r.role)
            db.add(ref)
        ref.ordinal = i
        if key not in retained:
            ref.ref_mode = r.ref_mode
            ref.pinned_version_id = r.pinned_version_id if r.ref_mode == "pinned" else None
            ref.usage = resolved_usage.get(key) or (r.usage.model_dump() if r.usage else ref.usage or {})
    db.flush()
    return list_asset_refs(db, ctx.project.id, s.id)


def list_asset_refs(db: Session, project_id: uuid.UUID, shot_id: uuid.UUID) -> list[schemas.AssetRefOut]:
    _get(db, project_id, shot_id)
    rows = db.execute(
        select(ShotAssetRef, Asset.name, Asset.deleted_at)
        .outerjoin(Asset, (Asset.id == ShotAssetRef.asset_id) & (Asset.project_id == project_id))
        .where(ShotAssetRef.shot_id == shot_id).order_by(ShotAssetRef.ordinal)
    )
    return [
        schemas.AssetRefOut.model_validate(ref).model_copy(
            update={"asset_name": name, "asset_removed": deleted is not None}
        )
        for ref, name, deleted in rows
    ]


# ── 状态机 ──
def transition(db: Session, ctx: ProjectContext, shot_id: uuid.UUID, to: ProductionStatus) -> Shot:
    s = _get(db, ctx.project.id, shot_id, lock=True)
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
def compose_prompt(db: Session, project_id: uuid.UUID, shot_id: uuid.UUID, media_type="image", *, inputs=None) -> schemas.ComposeOut:
    s = _get(db, project_id, shot_id)
    if s.prompt_override:
        return schemas.ComposeOut(final_prompt=s.prompt_override, parts=[s.prompt_override], overridden=True)

    parts: list[str] = []
    scene = db.get(Scene, s.scene_id)
    if scene and scene.summary:
        parts.append(scene.summary)

    from app.modules.shot.inputs import resolved

    board = s.storyboard or {}
    if scene and scene.visual_brief and board.get("inherit_scene", True):
        parts.append(scene.visual_brief)
    for entry in inputs if inputs is not None else resolved(db, s):
        parts.extend(entry["parts"])
    parts.append(board.get("still_prompt") or s.description or s.title or "")
    if media_type == "video":
        parts.append(board.get("motion_prompt") or s.description or "")

    # 分镜规格 → 提示词术语(景别 / 机位 / 运镜 / 画面比例 自动拼入)
    specs = board if media_type == "video" else {k: v for k, v in board.items() if k != "camera_move"}
    parts.extend(_storyboard_prompt_parts(specs))

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
