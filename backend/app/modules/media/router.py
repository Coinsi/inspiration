"""媒体读取:按 blob 哈希返回二进制(供 <img>/<video> 直接引用)。

为支持 <img> 标签(无法带 Authorization 头),允许通过 ?token= 传 JWT;
也兼容标准 Authorization 头。仅项目成员可读。
"""

import uuid

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.security import decode_access_token
from app.models.asset import Asset, AssetReferenceImage
from app.models.generation import Generation
from app.models.identity import Membership, Project, User
from app.models.narrative import Novel
from app.models.storage import Blob
from app.models.timeline import AudioTrack, Timeline
from app.storage import cas

router = APIRouter(prefix="/projects/{project_id}", tags=["media"])


def _media_user_id(token: str | None, authorization: str | None) -> uuid.UUID:
    raw = token
    if raw is None and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:]
    if not raw:
        raise Unauthorized("缺少令牌")
    try:
        return uuid.UUID(decode_access_token(raw)["sub"])
    except Exception as exc:  # noqa: BLE001
        raise Unauthorized("令牌无效") from exc


@router.get("/blobs/{blob_hash}")
def get_blob(
    project_id: uuid.UUID,
    blob_hash: str,
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    range: str | None = Header(None),
    download: bool = Query(False),
    db: Session = Depends(get_db),
):
    user_id = _media_user_id(token, authorization)
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
    try:
        data = cas.read_bytes(blob)
    except Exception as exc:  # noqa: BLE001
        raise NotFound("资源数据不可读(可能存储后端已切换或对象缺失)") from exc
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
    if download:
        ext = "mp4" if blob.mime.startswith("video/") else "png"
        headers["Content-Disposition"] = (
            f'attachment; filename="inspiration-{blob_hash[:12]}.{ext}"'
        )
    if range:
        import re

        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range)
        if not match or not any(match.groups()):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{len(data)}"})
        left, right = match.groups()
        start = int(left) if left else max(0, len(data) - int(right))
        end = min(int(right), len(data) - 1) if left and right else len(data) - 1
        if start > end or start >= len(data):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{len(data)}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        return Response(
            content=data[start : end + 1], status_code=206, media_type=blob.mime, headers=headers
        )
    return Response(content=data, media_type=blob.mime, headers=headers)
