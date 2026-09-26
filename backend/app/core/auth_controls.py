import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.auth import AuthAttempt, RevokedToken


class TooManyAttempts(AppError):
    code, http_status = "TOO_MANY_ATTEMPTS", 429


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def revocation_key(token):
    """Call after JWT verification; base64url aliases must identify the same token."""
    from jwt.utils import base64url_decode

    return hashlib.sha256(base64url_decode(token.rsplit(".", 1)[1])).hexdigest()


def is_revoked(db, token):
    return (
        db.get(RevokedToken, revocation_key(token)) is not None
        or db.get(RevokedToken, token_hash(token)) is not None
    )


def validate_user_session(user, payload):
    from app.core.errors import Unauthorized
    if user is None or not user.is_active:
        raise Unauthorized("用户不存在或已禁用")
    if payload.get("ver", 0) != user.auth_version:
        raise Unauthorized("账户安全信息已更新，请重新登录")


def limit_auth(db, request: Request, action):
    # Use the trusted ASGI peer address; never trust a caller-supplied forwarding header.
    peer = request.client.host if request.client else "unknown"
    window, limit = (60, 30) if action == "login" else (3600, 10)
    now = datetime.now(UTC)
    bucket = int(now.timestamp()) // window
    key = token_hash(f"{action}:{peer}:{bucket}")
    expiry = datetime.fromtimestamp((bucket + 1) * window, UTC)
    # Commit attempts even when the enclosing login request fails and rolls back.
    with Session(bind=db.get_bind()) as counter:
        count = counter.scalar(
            insert(AuthAttempt)
            .values(key=key, count=1, expires_at=expiry)
            .on_conflict_do_update(
                index_elements=[AuthAttempt.key], set_={"count": AuthAttempt.count + 1}
            )
            .returning(AuthAttempt.count)
        )
        counter.execute(delete(AuthAttempt).where(AuthAttempt.expires_at < now - timedelta(days=1)))
        counter.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
        counter.commit()
    if count > limit:
        raise TooManyAttempts(
            "尝试过于频繁，请稍后再试", {"retry_after": max(1, int((expiry - now).total_seconds()))}
        )


def revoke(db, token, payload):
    db.execute(
        insert(RevokedToken)
        .values(
            token_hash=revocation_key(token), expires_at=datetime.fromtimestamp(payload["exp"], UTC)
        )
        .on_conflict_do_nothing()
    )
