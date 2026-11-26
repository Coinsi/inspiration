"""Read an asset's scene appearances, including scenes that have no storyboard yet."""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.kernel.graph import GraphService
from app.models.enums import VersionStatus
from app.models.narrative import Chapter, Novel, Scene, SceneElement, Script
from app.modules.asset import schemas, service


def list_appearances(
    db: Session, project_id: UUID, asset_id: UUID
) -> list[schemas.AssetAppearance]:
    service.get(db, project_id, asset_id)
    shots_by_scene: dict[UUID, dict[str, schemas.AppearanceShot]] = defaultdict(dict)
    for ref in GraphService(db).asset_usages(asset_id):
        shots = shots_by_scene[UUID(ref["scene_id"])]
        key = ref["shot_id"]
        if key not in shots:
            shots[key] = schemas.AppearanceShot(
                id=key, title=ref["shot_title"], ordinal=ref["shot_ordinal"]
            )
        pinned = ref["pinned_version_no"]
        if pinned is not None and pinned not in shots[key].pinned_versions:
            shots[key].pinned_versions.append(pinned)

    # A removed scene can still own live shots. Only current script scenes can supply a look.
    current_scene = and_(
        Script.deleted_at.is_(None),
        or_(Scene.script_anchor.is_(None), Scene.script_position.is_not(None)),
    )
    rows = db.execute(
        select(
            Scene.id,
            Scene.title,
            Scene.ordinal,
            Scene.status,
            current_scene.label("current_scene"),
            Script.id.label("script_id"),
            Script.title.label("script_title"),
            Novel.title.label("novel_title"),
            Chapter.id.label("chapter_id"),
            Chapter.ordinal.label("chapter_ordinal"),
            Chapter.title.label("chapter_title"),
            SceneElement.id.label("member_id"),
            SceneElement.name.label("member_name"),
            func.left(SceneElement.direction, 160).label("direction_excerpt"),
            SceneElement.reference_hash,
        )
        .select_from(Scene)
        .join(Script, and_(Script.id == Scene.script_id, Script.project_id == project_id))
        .outerjoin(
            Chapter,
            Chapter.id == func.coalesce(Scene.adapted_from_chapter_id, Script.source_chapter_id),
        )
        .outerjoin(
            Novel,
            and_(
                Novel.id == Chapter.novel_id,
                Novel.project_id == project_id,
                Novel.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            SceneElement,
            and_(
                SceneElement.scene_id == Scene.id,
                SceneElement.asset_id == asset_id,
                SceneElement.active.is_(True),
                current_scene,
            ),
        )
        .where(
            Scene.project_id == project_id,
            Scene.deleted_at.is_(None),
            or_(SceneElement.id.is_not(None), Scene.id.in_(shots_by_scene.keys())),
        )
        .order_by(
            Novel.title,
            Novel.id,
            Chapter.ordinal,
            Script.title,
            Script.id,
            Scene.ordinal,
            Scene.id,
            SceneElement.name,
            SceneElement.id,
        )
    )
    result: dict[UUID, schemas.AssetAppearance] = {}
    for row in rows:
        if row.id not in result:
            result[row.id] = schemas.AssetAppearance(
                scene_id=row.id,
                title=row.title,
                ordinal=row.ordinal,
                locked=row.status == VersionStatus.locked,
                removed_from_script=not row.current_scene,
                script_id=row.script_id,
                script_title=row.script_title,
                novel_title=row.novel_title,
                chapter_id=row.chapter_id if row.novel_title is not None else None,
                chapter_ordinal=row.chapter_ordinal if row.novel_title is not None else None,
                chapter_title=row.chapter_title if row.novel_title is not None else None,
                shots=list(shots_by_scene.get(row.id, {}).values()),
            )
        if row.member_id is not None:
            result[row.id].members.append(
                schemas.AppearanceMember(
                    id=row.member_id,
                    name=row.member_name,
                    direction_excerpt=row.direction_excerpt or "",
                    reference_hash=row.reference_hash,
                )
            )
    return list(result.values())
