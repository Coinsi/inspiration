"""安全:密码哈希 + JWT 签发/校验。"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings


def _to72(raw: str) -> bytes:
    # bcrypt 仅取前 72 字节,显式截断避免新版本抛错
    return raw.encode("utf-8")[:72]


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(_to72(raw), bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to72(raw), hashed.encode("ascii"))
    except ValueError:
        return False


def create_access_token(subject: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
