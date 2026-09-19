"""Validated cover upload; originals stay private in the existing media store."""

import hashlib
import io

from PIL import Image
from sqlalchemy import select, text

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict
from app.models.identity import Project
from app.modules.identity import schemas, service
from app.storage import cas


def read_cover(file):
    if file is None:
        return None
    raw = file.file.read(10 * 1024 * 1024 + 1)
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise CapabilityUnsupported("封面图片不能超过 10 MB")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(im.format)
            if not mime or im.width * im.height > 40_000_000 or getattr(im, "is_animated", False):
                raise ValueError()
            im.load()
            return raw, mime, im.width, im.height
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CapabilityUnsupported(
            "请选择有效的静态 JPG、PNG 或 WebP 图片（最多4000万像素）"
        ) from exc


def store(db, data):
    if data is None:
        return None
    raw, mime, width, height = data
    return cas.put_bytes(db, raw, mime, width=width, height=height).hash


def create(db, user, name, description, request_key, file):
    data = read_cover(file)
    # Serialize retries even before the new project exists. The lock ends with the transaction.
    lock_key = int.from_bytes(hashlib.sha256(request_key.bytes).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    old = db.get(Project, request_key)
    payload = schemas.ProjectIn(name=name, description=description or None)
    if old:
        digest = hashlib.sha256(data[0]).hexdigest() if data else None
        if (
            old.owner_id != user.id
            or old.deleted_at
            or old.name != payload.name
            or old.description != payload.description
            or old.cover_blob_hash != digest
        ):
            raise Conflict("此创建请求已经使用，请刷新后重试")
        return old
    project = service.create_project(db, user, payload, project_id=request_key)
    project.cover_blob_hash = store(db, data)
    db.flush()
    return project


def change(db, ctx, file=None):
    data = read_cover(file)
    project = db.scalar(
        select(Project)
        .where(Project.id == ctx.project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    project.cover_blob_hash = store(db, data)
    audit.record(
        db,
        action="project.cover",
        user_id=ctx.user.id,
        project_id=project.id,
        target_type="project",
        target_id=project.id,
        detail={"cover": project.cover_blob_hash},
    )
    db.flush()
    return project
