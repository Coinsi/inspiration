"""Google authorization-code login; browser URLs never contain application tokens."""

import logging
import secrets
from typing import Literal
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_controls import limit_auth, revocation_key
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import create_access_token, decode_access_token
from app.modules.identity import google

router = APIRouter(prefix="/auth/google", tags=["auth"])
logger = logging.getLogger(__name__)


class GoogleCallbackAccessFilter(logging.Filter):
    """Keep single-use OAuth codes out of Uvicorn request logs."""

    def filter(self, record):
        args = record.args
        return not (
            isinstance(args, tuple)
            and len(args) >= 3
            and isinstance(args[2], str)
            and args[2].split("?", 1)[0] == google.CALLBACK_PATH
        )


logging.getLogger("uvicorn.access").addFilter(GoogleCallbackAccessFilter())
DB = Depends(get_db, scope="function")
BEARER = Depends(HTTPBearer(auto_error=False))


class GoogleStartIn(BaseModel):
    next: str = Field(default="/projects", max_length=2048)
    mode: Literal["login", "link"] = "login"


def _no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _set_cookie(response, name, value, max_age):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=google.COOKIE_PATH,
        secure=google.secure_cookie(),
        httponly=True,
        samesite="lax",
    )


def _clear_cookies(response):
    for name in (google.BROWSER_COOKIE, google.EXCHANGE_COOKIE):
        response.delete_cookie(
            name,
            path=google.COOKIE_PATH,
            secure=google.secure_cookie(),
            httponly=True,
            samesite="lax",
        )


def _complete(error=None):
    destination = google.frontend_origin() + "/auth/google/complete"
    if error:
        destination += "?" + urlencode({"error": error})
    return _no_cache(RedirectResponse(destination, status_code=303))


@router.post("/start")
def start(
    data: GoogleStartIn,
    request: Request,
    response: Response,
    db: Session = DB,
    cred: HTTPAuthorizationCredentials | None = BEARER,
):
    google.require_enabled()
    google.require_origin(request)
    limit_auth(db, request, "google_start")
    payload = {"next": google.safe_next(data.next), "mode": data.mode}
    if data.mode == "link":
        user = get_current_user(cred=cred, db=db)
        try:
            session_expires_at = decode_access_token(cred.credentials)["exp"]
        except jwt.PyJWTError as exc:
            raise google.GoogleAuthError("google_session_expired", 401) from exc
        payload.update(
            user_id=str(user.id),
            auth_version=user.auth_version,
            session_key=revocation_key(cred.credentials),
            session_expires_at=session_expires_at,
        )
    browser = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(32)
    payload.update(verifier=verifier, nonce=nonce)
    state = google.store_transaction(
        db, browser=browser, kind="state", payload=payload, ttl=google.STATE_TTL
    )
    _clear_cookies(response)
    _set_cookie(response, google.BROWSER_COOKIE, browser, google.STATE_TTL)
    _no_cache(response)
    return {
        "authorization_url": google.authorization_url(state=state, verifier=verifier, nonce=nonce)
    }


@router.get("/callback")
def callback(request: Request, db: Session = DB):
    payload = {}
    try:
        google.require_enabled()
        browser = request.cookies.get(google.BROWSER_COOKIE, "")
        payload = google.consume_transaction(
            db, token=request.query_params.get("state", ""), browser=browser, kind="state"
        )
        if request.query_params.get("error"):
            raise google.GoogleAuthError("google_cancelled")
        code = request.query_params.get("code", "")
        if not code or len(code) > 4096:
            raise google.GoogleAuthError("google_token_invalid")
        identity = google.verify_google_code(
            code=code, verifier=payload["verifier"], nonce=payload["nonce"]
        )
        # A savepoint isolates account writes so every failure rolls back cleanly;
        # the state has already been irrevocably consumed in its own transaction.
        with db.begin_nested():
            user = google.resolve_user(db, identity, payload)
            exchange_payload = {
                "user_id": str(user.id),
                "auth_version": user.auth_version,
                "next": payload["next"],
                "linked": payload["mode"] == "link",
            }
            if payload["mode"] == "link":
                exchange_payload["session_key"] = payload["session_key"]
                exchange_payload["session_expires_at"] = payload["session_expires_at"]
            exchange = google.store_transaction(
                db,
                browser=browser,
                kind="exchange",
                payload=exchange_payload,
                ttl=google.EXCHANGE_TTL,
            )
        # Finish writes before issuing cookies; a failed commit must not produce
        # a success redirect pointing at an exchange that was never persisted.
        db.commit()
        result = _complete()
        _set_cookie(result, google.BROWSER_COOKIE, browser, google.EXCHANGE_TTL)
        _set_cookie(result, google.EXCHANGE_COOKIE, exchange, google.EXCHANGE_TTL)
        return result
    except IntegrityError:
        # Unique constraints close concurrent first-login and binding races.
        db.rollback()
        error = (
            "google_identity_conflict" if payload.get("mode") == "link" else "google_email_conflict"
        )
    except google.GoogleAuthError as exc:
        db.rollback()
        error = exc.code
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # Never log request URLs, OAuth tokens, client secrets, or DB payloads.
        logger.warning("Google callback failed (%s)", type(exc).__name__)
        error = "google_internal_error"
    result = _complete(error)
    _clear_cookies(result)
    return result


@router.post("/exchange")
def exchange(request: Request, db: Session = DB):
    google.require_enabled()
    google.require_origin(request)
    limit_auth(db, request, "google_exchange")
    try:
        payload = google.consume_transaction(
            db,
            token=request.cookies.get(google.EXCHANGE_COOKIE, ""),
            browser=request.cookies.get(google.BROWSER_COOKIE, ""),
            kind="exchange",
        )
        user = google.validate_link_session(db, payload)
        result = JSONResponse(
            {
                "access_token": create_access_token(str(user.id), {"ver": user.auth_version}),
                "next": google.safe_next(payload["next"]),
                "linked": payload["linked"],
            }
        )
    except google.GoogleAuthError as exc:
        result = JSONResponse(
            {"error": {"code": exc.code, "message": exc.message, "detail": {}}},
            status_code=exc.http_status,
        )
    _clear_cookies(result)
    return _no_cache(result)
