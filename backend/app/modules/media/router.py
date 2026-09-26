"""媒体读取:按 blob 哈希返回二进制(供 <img>/<video> 直接引用)。

为支持 <img> 标签(无法带 Authorization 头),允许通过 ?token= 传 JWT;
也兼容标准 Authorization 头。仅项目成员可读。
"""

import uuid

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.database import get_db
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.security import decode_access_token
from app.models.asset import Asset, AssetReferenceImage
from app.models.generation import Generation
from app.models.identity import Membership, Project, User
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex, MediaSegment
from app.models.narrative import Novel
from app.models.storage import Blob
from app.models.timeline import AudioTrack, Timeline
from app.storage import cas

router = APIRouter(prefix="/projects/{project_id}", tags=["media"])


def _media_user_id(token: str | None, authorization: str | None, db: Session) -> uuid.UUID:
    raw = token
    if raw is None and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:]
    if not raw:
        raise Unauthorized("缺少令牌")
    try:
        payload = decode_access_token(raw)
        user_id = uuid.UUID(payload["sub"])
    except Exception as exc:  # noqa: BLE001
        raise Unauthorized("令牌无效") from exc
    from app.core.auth_controls import is_revoked, validate_user_session
    if is_revoked(db, raw):
        raise Unauthorized("登录已退出，请重新登录")
    validate_user_session(db.get(User, user_id), payload)
    return user_id


@router.get("/blobs/{blob_hash}")
def get_blob(
    project_id: uuid.UUID,
    blob_hash: str,
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    range: str | None = Header(None),
    download: bool = Query(False),
    db: Session = Depends(get_db, scope="function"),
):
    user_id = _media_user_id(token, authorization, db)
    is_member = db.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .join(Project, Project.id == Membership.project_id)
        .where(
            Membership.project_id == project_id,
            Membership.user_id == user_id,
            User.is_active.is_(True),
            Project.deleted_at.is_(None),
        )
    )
    if is_member is None:
        raise Forbidden("非项目成员")
    owned = db.scalar(
        select(
            or_(
                select(Project.id)
                .where(Project.id == project_id, Project.cover_blob_hash == blob_hash)
                .exists(),
                select(MediaSegment.id)
                .join(MediaIndex).join(MediaVersion).join(LibraryMedia)
                .where(LibraryMedia.project_id == project_id,
                       LibraryMedia.deleted_at.is_(None),
                       MediaSegment.frames.contains([{"hash": blob_hash}])).exists(),
                select(MediaVersion.id)
                .join(LibraryMedia)
                .where(
                    LibraryMedia.project_id == project_id,
                    LibraryMedia.deleted_at.is_(None),
                    or_(
                        MediaVersion.original_hash == blob_hash,
                        MediaVersion.proxy_hash == blob_hash,
                        MediaVersion.poster_hash == blob_hash,
                    ),
                )
                .exists(),
                select(Generation.id)
                .where(
                    Generation.project_id == project_id,
                    or_(
                        Generation.output_blob_hash == blob_hash,
                        Generation.thumbnail_hash == blob_hash,
                    ),
                )
                .exists(),
                select(Asset.id)
                .where(Asset.project_id == project_id, Asset.representative_blob_hash == blob_hash)
                .exists(),
                select(AssetReferenceImage.id)
                .join(Asset, Asset.id == AssetReferenceImage.asset_id)
                .where(Asset.project_id == project_id, AssetReferenceImage.blob_hash == blob_hash)
                .exists(),
                select(Novel.id)
                .where(Novel.project_id == project_id, Novel.original_blob_hash == blob_hash)
                .exists(),
                select(AudioTrack.id)
                .join(Timeline, Timeline.id == AudioTrack.timeline_id)
                .where(Timeline.project_id == project_id, AudioTrack.blob_hash == blob_hash)
                .exists(),
            )
        )
    )
    if not owned:
        raise NotFound("资源不存在")
    blob = db.get(Blob, blob_hash)
    if blob is None:
        raise NotFound("资源不存在")
    # Streaming must not hold a DB pool connection for the duration of a long video.
    db.expunge(blob)
    db.commit()
    size = blob.size_bytes
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
    if download:
        ext = {
            "video/mp4": "mp4",
            "video/quicktime": "mov",
            "video/x-matroska": "mkv",
            "video/webm": "webm",
            "audio/mp4": "m4a",
            "audio/wav": "wav",
            "audio/mpeg": "mp3",
            "audio/ogg": "ogg",
            "audio/flac": "flac",
            "image/png": "png",
            "image/jpeg": "jpg",
            "text/plain": "txt",
        }.get(blob.mime, "bin")
        headers["Content-Disposition"] = (
            f'attachment; filename="inspiration-{blob_hash[:12]}.{ext}"'
        )
    start, end = 0, size - 1
    if range:
        import re

        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range)
        if not match or not any(match.groups()):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        left, right = match.groups()
        start = int(left) if left else max(0, size - int(right))
        end = min(int(right), size - 1) if left and right else size - 1
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1
    manager = cas.open_range(blob, start, length)
    try:
        stream = manager.__enter__()
    except Exception as exc:
        raise NotFound("资源数据不可读(可能存储后端已切换或对象缺失)") from exc
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            manager.__exit__(None, None, None)

    def chunks():
        remaining = length
        try:
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            close()

    headers["Content-Length"] = str(length)
    return StreamingResponse(
        chunks(),
        status_code=206 if range else 200,
        media_type=blob.mime,
        headers=headers,
        background=BackgroundTask(close),
    )
