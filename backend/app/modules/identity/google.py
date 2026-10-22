"""Google OIDC verification and browser-bound, single-use transaction storage."""

import hashlib
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlencode, urlsplit

import httpx
import jwt
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.auth_controls import token_hash
from app.core.config import settings
from app.core.errors import AppError, Forbidden
from app.models.auth import OAuthTransaction, RevokedToken
from app.models.identity import User, UserIdentity

ISSUER = "https://accounts.google.com"
AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
CALLBACK_PATH = "/api/v1/auth/google/callback"
COOKIE_PATH = "/api/v1/auth/google"
BROWSER_COOKIE = "oauth_google_browser"
EXCHANGE_COOKIE = "oauth_google_exchange"
STATE_TTL = 600
EXCHANGE_TTL = 60
_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_jwks_lock = threading.Lock()
_jwks_cache: dict = {}
_jwks_expires_at = 0.0

ERROR_MESSAGES = {
    "google_unavailable": "Google 登录尚未配置，请使用其他登录方式",
    "google_cancelled": "已取消 Google 授权",
    "google_state_invalid": "Google 登录请求已过期或不属于当前浏览器，请重新开始",
    "google_token_invalid": "Google 身份验证失败，请重新尝试",
    "google_network_error": "暂时无法连接 Google，请稍后重试",
    "google_registration_disabled": "暂未开放 Google 注册，请先登录已有账户并绑定",
    "google_email_unverified": "请先验证 Google 账户邮箱后再尝试",
    "google_email_conflict": "该邮箱已有账户，请先用原账号登录，再在账户安全中绑定 Google",
    "google_identity_conflict": "此 Google 身份已绑定其他账户",
    "google_account_disabled": "此账户已禁用，无法登录",
    "google_session_expired": "账户登录状态已变化，请重新登录后绑定 Google",
    "google_exchange_invalid": "Google 登录结果已过期或已使用，请重新登录",
    "google_internal_error": "Google 登录暂时失败，请重新尝试",
}


class GoogleAuthError(AppError):
    def __init__(self, code: str, http_status: int = 400):
        self.code = code
        self.http_status = http_status
        super().__init__(ERROR_MESSAGES[code])


@dataclass(frozen=True)
class VerifiedGoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    name: str = ""


def _origin(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError:
        return ""
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in raw)
        or "\\" in raw
    ):
        return ""
    if parsed.scheme != "https" and not (
        settings.is_dev
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def frontend_origin() -> str:
    origin = _origin(settings.google_frontend_url)
    if not origin or urlsplit(settings.google_frontend_url).path not in {"", "/"}:
        return ""
    return origin


def google_enabled() -> bool:
    origin = frontend_origin()
    redirect = _origin(settings.google_redirect_uri)
    if not origin or not redirect:
        return False
    frontend = urlsplit(origin)
    callback = urlsplit(settings.google_redirect_uri)
    return bool(
        settings.google_client_id.strip()
        and settings.google_client_secret.strip()
        and callback.path == CALLBACK_PATH
        and frontend.hostname == callback.hostname
        and frontend.scheme == callback.scheme
        and origin == redirect
    )


def require_enabled():
    if not google_enabled():
        raise GoogleAuthError("google_unavailable", 503)


def require_origin(request):
    if request.headers.get("origin") != frontend_origin():
        raise Forbidden("登录请求来源无效，请从本站页面发起")


def secure_cookie() -> bool:
    return urlsplit(settings.google_redirect_uri).scheme == "https"


def safe_next(raw: str) -> str:
    if not raw or len(raw) > 2048 or "\\" in raw or any(ord(c) < 32 for c in raw):
        return "/projects"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "/projects"
    if parsed.scheme or parsed.netloc:
        return "/projects"
    # Reject browser-normalized traversal (including encoded separators) before
    # checking the protected-route prefix. Queries and fragments remain intact.
    path = parsed.path
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    if (
        "%" in path
        or "\\" in path
        or "//" in path
        or any(ord(c) < 32 for c in path)
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        return "/projects"
    allowed = (
        parsed.path == "/projects"
        or parsed.path.startswith("/projects/")
        or parsed.path == "/admin/website"
        or parsed.path == "/admin/blog"
        or parsed.path.startswith("/admin/blog/")
    )
    return raw if allowed else "/projects"


def authorization_url(*, state: str, verifier: str, nonce: str) -> str:
    challenge = jwt.utils.base64url_encode(hashlib.sha256(verifier.encode()).digest()).decode()
    return (
        AUTHORIZATION_URL
        + "?"
        + urlencode(
            {
                "client_id": settings.google_client_id,
                "redirect_uri": settings.google_redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
    )


def store_transaction(db, *, browser: str, kind: str, payload: dict, ttl: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.execute(delete(OAuthTransaction).where(OAuthTransaction.expires_at < now))
    db.add(
        OAuthTransaction(
            token_hash=token_hash(token),
            browser_hash=token_hash(browser),
            kind=kind,
            payload=payload,
            expires_at=now + timedelta(seconds=ttl),
        )
    )
    db.flush()
    return token


def consume_transaction(db, *, token: str, browser: str, kind: str) -> dict:
    error = "google_state_invalid" if kind == "state" else "google_exchange_invalid"
    if not _OPAQUE_TOKEN.fullmatch(token) or not _OPAQUE_TOKEN.fullmatch(browser):
        raise GoogleAuthError(error)
    # The delete commits independently before network I/O or account changes. A failed
    # verification must not resurrect a consumed state when the request rolls back.
    with Session(bind=db.get_bind()) as transaction:
        payload = transaction.scalar(
            delete(OAuthTransaction)
            .where(
                OAuthTransaction.token_hash == token_hash(token),
                OAuthTransaction.browser_hash == token_hash(browser),
                OAuthTransaction.kind == kind,
                OAuthTransaction.expires_at > datetime.now(UTC),
            )
            .returning(OAuthTransaction.payload)
        )
        transaction.commit()
    if payload is None:
        raise GoogleAuthError(error)
    return payload


def _fetch_keys() -> dict:
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(JWKS_URL)
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
            raise ValueError("invalid keys")
        keys = {}
        for item in body["keys"]:
            if (
                isinstance(item, dict)
                and isinstance(item.get("kid"), str)
                and item.get("kty") == "RSA"
                and item.get("use", "sig") == "sig"
                and item.get("alg", "RS256") == "RS256"
            ):
                keys[item["kid"]] = jwt.PyJWK.from_dict(item).key
        if not keys:
            raise ValueError("empty keys")
        return keys
    except httpx.HTTPError as exc:
        raise GoogleAuthError("google_network_error", 502) from exc
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        raise GoogleAuthError("google_token_invalid") from exc


def _signing_key(kid: str):
    global _jwks_cache, _jwks_expires_at
    with _jwks_lock:
        now = time.monotonic()
        if now >= _jwks_expires_at or kid not in _jwks_cache:
            _jwks_cache = _fetch_keys()
            _jwks_expires_at = now + 600
        key = _jwks_cache.get(kid)
    if key is None:
        raise GoogleAuthError("google_token_invalid")
    return key


def verify_google_code(*, code: str, verifier: str, nonce: str) -> VerifiedGoogleIdentity:
    require_enabled()
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_redirect_uri,
                    "code_verifier": verifier,
                },
            )
            if response.status_code in {400, 401}:
                raise GoogleAuthError("google_token_invalid")
            response.raise_for_status()
            body = response.json()
        token = body.get("id_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or len(token) > 16384:
            raise ValueError("missing ID token")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or len(kid) > 255:
            raise ValueError("invalid signing algorithm")
        claims = jwt.decode(
            token,
            _signing_key(kid),
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=[ISSUER, "accounts.google.com"],
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
        if (
            not isinstance(claims["sub"], str)
            or not claims["sub"]
            or len(claims["sub"]) > 255
            or not isinstance(claims["nonce"], str)
            or not secrets.compare_digest(claims["nonce"], nonce)
            or ("azp" in claims and claims["azp"] != settings.google_client_id)
            or (
                isinstance(claims["aud"], list)
                and len(claims["aud"]) > 1
                and claims.get("azp") != settings.google_client_id
            )
        ):
            raise ValueError("invalid ID token claims")
        if claims.get("email_verified") is not True:
            raise GoogleAuthError("google_email_unverified")
        email = str(TypeAdapter(EmailStr).validate_python(claims.get("email"))).lower()
        if len(email) > 255:
            raise ValueError("invalid email")
        name = claims.get("name")
        return VerifiedGoogleIdentity(
            subject=claims["sub"],
            email=email,
            email_verified=True,
            name=name.strip()[:128] if isinstance(name, str) else "",
        )
    except httpx.HTTPError as exc:
        raise GoogleAuthError("google_network_error", 502) from exc
    except (ValueError, TypeError, ValidationError, jwt.PyJWTError) as exc:
        raise GoogleAuthError("google_token_invalid") from exc


def _active_user(db, user_id: str, auth_version: int | None = None) -> User:
    user = db.scalar(
        select(User)
        .where(User.id == uuid.UUID(user_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active:
        raise GoogleAuthError("google_account_disabled", 403)
    if auth_version is not None and user.auth_version != auth_version:
        raise GoogleAuthError("google_session_expired", 401)
    return user


def validate_link_session(db, payload: dict) -> User:
    if payload.get("session_expires_at", float("inf")) <= datetime.now(UTC).timestamp():
        raise GoogleAuthError("google_session_expired", 401)
    user = _active_user(db, payload["user_id"], payload["auth_version"])
    if payload.get("session_key") and db.get(RevokedToken, payload["session_key"]) is not None:
        raise GoogleAuthError("google_session_expired", 401)
    return user


def resolve_user(db, identity: VerifiedGoogleIdentity, payload: dict) -> User:
    if not identity.email_verified:
        raise GoogleAuthError("google_email_unverified")
    linked = db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == "google",
            UserIdentity.issuer == ISSUER,
            UserIdentity.subject == identity.subject,
        )
    )
    if payload["mode"] == "link":
        user = validate_link_session(db, payload)
        if linked is not None and linked.user_id != user.id:
            raise GoogleAuthError("google_identity_conflict", 409)
    elif linked is not None:
        return _active_user(db, str(linked.user_id))
    else:
        if not settings.google_registration_enabled:
            raise GoogleAuthError("google_registration_disabled", 403)
        if db.scalar(select(User.id).where(func.lower(User.email) == identity.email.lower())):
            raise GoogleAuthError("google_email_conflict", 409)
        user = User(
            username="g_" + uuid.uuid4().hex,
            email=identity.email.lower(),
            password_hash=None,
            display_name=identity.name or identity.email.split("@", 1)[0][:128],
        )
        db.add(user)
        db.flush()
    if linked is None:
        db.add(
            UserIdentity(
                user_id=user.id, provider="google", issuer=ISSUER, subject=identity.subject
            )
        )
        db.flush()
    return user
