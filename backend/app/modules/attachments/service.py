"""Private conversation files, independent of library indexing and agent execution."""

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import or_, select

from app.core.config import settings
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.attachment import Attachment
from app.models.conversation import Conversation, ConversationMessage
from app.models.identity import Project
from app.models.storage import Blob
from app.modules.library import media_ingest, upload_budget
from app.modules.library.media_types import MAX_IMAGE_BYTES, display_name, for_filename
from app.storage import cas

CHUNK_SIZE = 8 * 1024 * 1024


def staging_path(attachment_id):
    # This directory is separate from library staging cleanup; no client paths are used.
    base = Path(settings.library_staging_dir).resolve() / "attachments"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{uuid.UUID(str(attachment_id))}.upload"


def get(db, ctx, attachment_id, *, lock=False):
    stmt = select(Attachment).where(
        Attachment.id == attachment_id,
        Attachment.project_id == ctx.project.id,
        Attachment.created_by == ctx.user.id,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    item = db.scalar(stmt)
    if item is None:
        raise NotFound("附件不存在或无权访问")
    return item


def describe(item):
    fields = (
        "title",
        "filename",
        "kind",
        "status",
        "uploaded_bytes",
        "size_bytes",
        "blob_hash",
        "thumbnail_hash",
        "duration_ms",
        "width",
        "height",
        "has_audio",
        "error",
        "favorite",
        "fingerprint",
        "chunk_hashes",
        "description",
        "tags",
        "metadata_revision",
    )
    return {
        **{key: getattr(item, key) for key in fields},
        "id": str(item.id),
        "source_type": "attachment",
        "chunk_size": CHUNK_SIZE,
        "saved_at": item.saved_at.isoformat() if item.saved_at else None,
        "hidden_at": item.hidden_at.isoformat() if item.hidden_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "conversation_id": str(item.conversation_id) if item.conversation_id else None,
        "message_id": str(item.message_id) if item.message_id else None,
    }


def begin(db, ctx, data):
    # Serialize idempotent admission before checking the shared upload budget.
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    existing = db.scalar(
        select(Attachment).where(
            Attachment.project_id == ctx.project.id,
            Attachment.created_by == ctx.user.id,
            Attachment.request_key == data.request_key,
        )
    )
    if existing:
        if (existing.filename, existing.size_bytes, existing.fingerprint) != (
            data.filename,
            data.size_bytes,
            data.fingerprint,
        ):
            raise Conflict("此上传记录属于另一个文件，请重新添加附件")
        return describe(existing)
    kind = for_filename(data.filename)
    limit = (
        min(MAX_IMAGE_BYTES, settings.library_max_upload_bytes)
        if kind == "image"
        else settings.library_max_upload_bytes
    )
    if data.size_bytes > limit:
        raise CapabilityUnsupported("附件大小超过限制", {"max_bytes": limit})
    upload_budget.reserve(db, data.size_bytes)
    item = Attachment(
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        request_key=data.request_key,
        title=display_name(data.filename),
        filename=data.filename,
        kind=kind,
        size_bytes=data.size_bytes,
        fingerprint=data.fingerprint,
    )
    db.add(item)
    db.flush()
    return describe(item)


def append(db, ctx, attachment_id, offset, upload):
    item = get(db, ctx, attachment_id, lock=True)
    if item.status != "uploading":
        raise Conflict("附件当前不能继续上传，请查看上传状态")
    if offset != item.uploaded_bytes:
        raise Conflict("上传进度已变化，请刷新进度后继续", {"uploaded_bytes": item.uploaded_bytes})
    data = upload.file.read(CHUNK_SIZE + 1)
    expected = min(CHUNK_SIZE, item.size_bytes - offset)
    if not data or len(data) != expected:
        raise CapabilityUnsupported("附件分片大小不正确", {"expected_bytes": expected})
    path = staging_path(item.id)
    if offset and (not path.is_file() or path.stat().st_size < offset):
        raise Conflict("附件暂存文件不完整，请移除后重新添加")
    upload_budget.check_space(len(data))
    with path.open("r+b" if path.exists() else "wb") as target:
        # A request interrupted before its DB commit may have written extra bytes.
        target.truncate(offset)
        target.seek(offset)
        target.write(data)
        target.flush()
        os.fsync(target.fileno())
    item.uploaded_bytes += len(data)
    item.chunk_hashes = [*item.chunk_hashes, hashlib.sha256(data).hexdigest()]
    db.flush()
    return describe(item)


def complete(db, ctx, attachment_id):
    item = get(db, ctx, attachment_id, lock=True)
    if item.status == "ready":
        return describe(item)
    if item.status not in ("uploading", "failed"):
        raise Conflict("附件已取消，不能完成上传")
    if item.uploaded_bytes != item.size_bytes:
        raise Conflict("附件尚未上传完成", {"uploaded_bytes": item.uploaded_bytes})
    path = staging_path(item.id)
    if not path.is_file() or path.stat().st_size != item.size_bytes:
        item.status, item.error = "failed", "附件暂存文件不完整，请移除后重新添加"
        db.flush()
        return describe(item)
    try:
        # Decoding validates content, dimensions and duration; indexing is never involved.
        info = media_ingest.probe_media(path, item.kind)
    except (OSError, ValueError) as exc:
        item.status = "failed"
        # FFmpeg errors can contain filesystem paths and arbitrary input metadata.
        # Only our short, curated validation messages are suitable for the client.
        message = str(exc)
        item.error = (
            message
            if message.endswith("请重新上传") and len(message) < 120
            else "附件无法读取，请检查文件是否完整及格式是否受支持"
        )
        db.flush()
        return describe(item)
    blob = cas.put_file(
        db, path, info["mime"], **{key: info[key] for key in ("width", "height", "duration_ms")}
    )
    item.blob_hash = blob.hash
    # Original images are already validated. Thumbnail generation must not block use.
    item.thumbnail_hash = blob.hash if item.kind == "image" else None
    for key in ("width", "height", "duration_ms", "has_audio"):
        setattr(item, key, info[key])
    item.status, item.error = "ready", None
    db.flush()
    return describe(item)


def cleanup_staging(attachment_id):
    # Called only after commit: interrupted publication must retain resumable bytes.
    try:
        staging_path(attachment_id).unlink(missing_ok=True)
    except OSError:
        pass


def cancel(db, ctx, attachment_id):
    item = get(db, ctx, attachment_id, lock=True)
    if item.saved_at or item.message_id:
        raise Conflict("已发送的附件会随对话保存，不能作为草稿删除")
    item.status, item.error = "cancelled", None
    # Release this draft's CAS references; shared blobs are never deleted here.
    item.blob_hash = item.thumbnail_hash = None
    db.flush()
    return describe(item)


def resolve(db, ctx, attachment_id):
    """Return a fixed, authorized reference suitable for conversation/model context."""
    item = get(db, ctx, attachment_id)
    if item.status != "ready" or not item.blob_hash:
        raise Conflict("请等待附件上传完成后再发送")
    blob = db.get(Blob, item.blob_hash)
    if blob is None or not blob.mime.startswith(f"{item.kind}/"):
        raise Conflict("附件原文件不可用，请重新上传")
    return describe(item)


def bind(db, ctx, ids, conversation_id, message_id):
    """Adopt ready files in the same transaction that persists the user's message."""
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.project_id == ctx.project.id,
            Conversation.created_by == ctx.user.id,
        )
    )
    message = db.get(ConversationMessage, message_id)
    if (
        conversation is None
        or message is None
        or message.conversation_id != conversation.id
        or message.role != "user"
    ):
        raise NotFound("对话消息不存在或无权关联附件")
    if len(ids) > 8:
        raise CapabilityUnsupported("每条消息最多添加 8 个附件")
    # Fixed ordering avoids deadlocks when concurrent conversations reuse multiple files.
    unique = list(dict.fromkeys(uuid.UUID(str(value)) for value in ids))
    locked = {str(value): get(db, ctx, value, lock=True) for value in sorted(unique, key=str)}
    results = []
    for value in unique:
        item = locked[str(value)]
        resolve(db, ctx, value)
        if item.saved_at is None:
            item.saved_at = datetime.now(UTC)
            item.conversation_id, item.message_id = conversation.id, message.id
        results.append(describe(item))
    db.flush()
    return results


def listing(db, ctx, *, query="", kind=None, favorite=None, offset=0, limit=24):
    where = [
        Attachment.project_id == ctx.project.id,
        Attachment.created_by == ctx.user.id,
        Attachment.saved_at.is_not(None),
        Attachment.status == "ready",
        Attachment.hidden_at.is_(None),
    ]
    if query.strip():
        where.append(
            or_(
                Attachment.title.icontains(query.strip(), autoescape=True),
                Attachment.filename.icontains(query.strip(), autoescape=True),
            )
        )
    if kind:
        where.append(Attachment.kind == kind)
    if favorite is not None:
        where.append(Attachment.favorite.is_(favorite))
    rows = list(
        db.scalars(
            select(Attachment)
            .where(*where)
            .order_by(Attachment.saved_at.desc(), Attachment.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return {"items": [describe(row) for row in rows[:limit]], "has_more": len(rows) > limit}


def update(db, ctx, attachment_id, data):
    item = get(db, ctx, attachment_id, lock=True)
    if item.status != "ready":
        raise Conflict("请等待附件上传完成")
    metadata_changed = any(value is not None for value in (data.title, data.description, data.tags))
    if metadata_changed and item.metadata_revision != data.expected_revision:
        raise Conflict(
            "附件信息已更新，请刷新后再修改", {"metadata_revision": item.metadata_revision}
        )
    for key in ("title", "description", "tags"):
        value = getattr(data, key)
        if value is not None:
            setattr(item, key, value.strip() if isinstance(value, str) else value)
    if metadata_changed:
        item.metadata_revision += 1
    if data.favorite is not None:
        item.favorite = data.favorite
    db.flush()
    return describe(item)


def set_hidden(db, ctx, attachment_id, hidden: bool):
    item = get(db, ctx, attachment_id, lock=True)
    if item.saved_at is None:
        raise Conflict("尚未发送的附件请从草稿中移除")
    item.hidden_at = (item.hidden_at or datetime.now(UTC)) if hidden else None
    db.flush()
    # Existing messages and fixed references retain access; this is catalog organization.
    return {"restored": not hidden, "physical_delete": False}
