"""媒体读取:按 blob 哈希返回二进制(供 <img>/<video> 直接引用)。

为支持 <img> 标签(无法带 Authorization 头),允许通过 ?token= 传 JWT;
也兼容标准 Authorization 头。仅项目成员可读。
"""
import uuid

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.security import decode_access_token
from app.models.identity import Membership
from app.models.storage import Blob
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
    db: Session = Depends(get_db),
):
    user_id = _media_user_id(token, authorization)
    is_member = db.scalar(
        select(Membership).where(Membership.project_id == project_id, Membership.user_id == user_id)
    )
    if is_member is None:
        raise Forbidden("非项目成员")
    blob = db.get(Blob, blob_hash)
    if blob is None:
        raise NotFound("资源不存在")
    try:
        data = cas.read_bytes(blob)
    except Exception as exc:  # noqa: BLE001
        raise NotFound("资源数据不可读(可能存储后端已切换或对象缺失)") from exc
    return Response(content=data, media_type=blob.mime)
