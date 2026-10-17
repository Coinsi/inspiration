"""Explicit cover selection; uses the same validated upload and store as project covers."""

from sqlalchemy import select

from app.core import audit
from app.core.errors import NotFound
from app.models.narrative import Novel, Script
from app.modules.identity.covers import read_cover, store


def change(db, ctx, kind, target_id, file=None):
    model = Novel if kind == "novel" else Script
    target = db.scalar(
        select(model)
        .where(
            model.id == target_id,
            model.project_id == ctx.project.id,
            model.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if target is None:
        raise NotFound("故事或剧本不存在")
    target.cover_blob_hash = store(db, read_cover(file))
    audit.record(
        db,
        action=f"{kind}.cover",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type=kind,
        target_id=target.id,
        detail={"cover": target.cover_blob_hash},
    )
    db.flush()
    return {"cover_blob_hash": target.cover_blob_hash}
