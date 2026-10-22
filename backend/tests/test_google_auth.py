"""Google authentication security checks using local keys and a disposable database.

No Google credentials, live Google requests, application workers or production tables
are needed. The HTTP tests exercise the real auth dependencies and transactions.
"""

import base64
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core import auth_controls
from app.core.auth_controls import revocation_key
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AppError, app_error_handler
from app.core.security import create_access_token, decode_access_token
from app.models.auth import AuthAttempt, OAuthTransaction, RevokedToken
from app.models.identity import User, UserIdentity
from app.modules.identity import google
from app.modules.identity.router import router

ORIGIN = "https://app.example.test"
CALLBACK = ORIGIN + "/api/v1/auth/google/callback"
AUTH = "/api/v1/auth/google"
BROWSER_COOKIE = "oauth_google_browser"
EXCHANGE_COOKIE = "oauth_google_exchange"


def test_google_callback_authorization_codes_are_not_access_logged():
    from app.modules.identity.google_router import GoogleCallbackAccessFilter

    callback = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
        ("127.0.0.1", "GET", AUTH + "/callback?code=private-code", "1.1", 303), None,
    )
    other = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
        ("127.0.0.1", "POST", AUTH + "/start", "1.1", 200), None,
    )
    access_filter = GoogleCallbackAccessFilter()
    assert not access_filter.filter(callback)
    assert access_filter.filter(other)


@pytest.fixture
def google_settings(monkeypatch):
    values = {
        "google_client_id": "test-client.apps.googleusercontent.com",
        "google_client_secret": "local-test-google-secret",
        "google_redirect_uri": CALLBACK,
        "google_frontend_url": ORIGIN,
        "google_registration_enabled": True,
        "secret_key": "local-test-signing-key-with-at-least-32-bytes",
        "app_env": "test",
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(google, "_jwks_cache", {})
    monkeypatch.setattr(google, "_jwks_expires_at", 0.0)
    return values


@pytest.fixture
def oauth(tmp_path, google_settings):
    database_url = os.environ.get("TEST_DATABASE_URL")
    schema = None
    if database_url:
        admin = create_engine(database_url)
        schema = "test_google_" + uuid.uuid4().hex
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = admin.execution_options(schema_translate_map={None: schema})
    else:
        engine = create_engine(
            "sqlite:///" + str(tmp_path / "google-auth.sqlite3"),
            connect_args={"check_same_thread": False, "timeout": 10},
        )
    tables = [User, UserIdentity, AuthAttempt, RevokedToken, OAuthTransaction]
    for model in tables:
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    api = FastAPI()
    api.add_exception_handler(AppError, app_error_handler)
    api.include_router(router, prefix="/api/v1")

    def isolated_db():
        with sessions() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    api.dependency_overrides[get_db] = isolated_db
    try:
        with TestClient(api, base_url=ORIGIN, follow_redirects=False) as client:
            client.headers["Origin"] = ORIGIN
            yield client, sessions, api
    finally:
        if schema:
            # The name is generated above; only this test's random schema is removed.
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin.dispose()
        else:
            engine.dispose()


@pytest.fixture
def verified(monkeypatch):
    identity = google.VerifiedGoogleIdentity(
        subject="google-subject-123",
        email="new-google-user@example.com",
        email_verified=True,
        name="Google Writer",
    )
    calls = []

    def verify(**kwargs):
        calls.append(kwargs)
        return identity

    monkeypatch.setattr(google, "verify_google_code", verify)
    return identity, calls


def start(client, *, mode="login", next_path="/projects"):
    response = client.post(AUTH + "/start", json={"mode": mode, "next": next_path})
    assert response.status_code == 200, response.text
    authorization_url = response.json()["authorization_url"]
    return response, parse_qs(urlsplit(authorization_url).query)


def callback(client, state, **extra):
    return client.get(AUTH + "/callback", params={"state": state, "code": "test-code", **extra})


def error_code(response):
    assert response.is_redirect, response.text
    location = urlsplit(response.headers["location"])
    assert location.scheme + "://" + location.netloc == ORIGIN
    assert location.path == "/auth/google/complete"
    assert "access_token" not in response.headers["location"]
    return parse_qs(location.query)["error"][0]


def add_user(sessions, *, email="existing@example.com", subject=None, active=True):
    with sessions() as db:
        user = User(
            username="writer_" + hashlib.sha256(email.encode()).hexdigest()[:12],
            email=email,
            display_name="Existing Writer",
            password_hash="not-a-real-password-hash",
            is_active=active,
        )
        db.add(user)
        db.flush()
        if subject:
            db.add(
                UserIdentity(
                    user_id=user.id,
                    provider="google",
                    issuer="https://accounts.google.com",
                    subject=subject,
                )
            )
        db.commit()
        return user.id


def test_provider_is_hidden_without_complete_configuration(oauth, monkeypatch):
    client, _, _ = oauth
    advertised = client.get("/api/v1/auth/providers").json()["providers"]
    assert any(p["id"] == "google" and p["registration_enabled"] for p in advertised)
    for field in (
        "google_client_id",
        "google_client_secret",
        "google_redirect_uri",
        "google_frontend_url",
    ):
        with monkeypatch.context() as patch:
            patch.setattr(settings, field, "")
            assert client.get("/api/v1/auth/providers").json()["providers"] == []
            assert client.post(AUTH + "/start", json={"mode": "login"}).status_code >= 400


def test_start_uses_pkce_nonce_and_protected_browser_cookie(oauth):
    client, _, _ = oauth
    response, params = start(client)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == [settings.google_client_id]
    assert params["redirect_uri"] == [CALLBACK]
    assert set(params["scope"][0].split()) == {"openid", "email", "profile"}
    assert params["code_challenge_method"] == ["S256"]
    assert len(params["state"][0]) >= 32 and len(params["nonce"][0]) >= 32
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert "path=/api/v1/auth/google" in cookie
    assert "local-test-google-secret" not in response.text
    _, second = start(client)
    assert second["state"] != params["state"]
    assert second["nonce"] != params["nonce"]
    assert second["code_challenge"] != params["code_challenge"]


def test_google_start_allows_eleven_requests_but_limits_the_thirty_first(oauth, monkeypatch):
    client, _, _ = oauth

    class FixedMinute(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 10, 22, 12, 0, 1, tzinfo=UTC)
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(auth_controls, "datetime", FixedMinute)
    responses = [client.post(AUTH + "/start", json={}) for _ in range(31)]
    assert responses[10].status_code == 200
    assert all(response.status_code == 200 for response in responses[:30])
    assert responses[30].status_code == 429
    assert responses[30].json()["error"]["code"] == "TOO_MANY_ATTEMPTS"
    assert 0 < int(responses[30].headers["retry-after"]) <= 60


@pytest.mark.parametrize("browser", ["missing", "different"])
def test_callback_rejects_missing_or_mismatched_browser(oauth, verified, browser):
    client, _, _ = oauth
    _, params = start(client)
    client.cookies.clear()
    if browser == "different":
        client.cookies.set(BROWSER_COOKIE, "different-browser", path=AUTH)
    assert error_code(callback(client, params["state"][0])) == "google_state_invalid"
    assert verified[1] == []


def test_callback_rejects_expired_state_and_no_network_occurs(oauth, verified):
    client, sessions, _ = oauth
    _, params = start(client)
    with sessions.begin() as db:
        db.execute(
            update(OAuthTransaction).values(expires_at=datetime.now(UTC) - timedelta(seconds=5))
        )
    assert error_code(callback(client, params["state"][0])) == "google_state_invalid"
    assert verified[1] == []


def test_cancelled_callback_consumes_state(oauth, verified):
    client, _, _ = oauth
    _, params = start(client)
    state = params["state"][0]
    cookies = dict(client.cookies)
    assert error_code(callback(client, state, error="access_denied")) == "google_cancelled"
    client.cookies.update(cookies)
    assert error_code(callback(client, state)) == "google_state_invalid"
    assert verified[1] == []


@pytest.mark.parametrize(
    "next_path",
    [
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "/login",
        "/projects/../login",
        "/projects/%2e%2e/login",
        "/projects/%2f%2e%2e/login",
    ],
)
def test_next_cannot_escape_authorized_application_pages(oauth, verified, next_path):
    client, _, _ = oauth
    response = client.post(AUTH + "/start", json={"mode": "login", "next": next_path})
    if response.status_code >= 400:
        return
    params = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    completed = callback(client, params["state"][0])
    assert completed.headers["location"] == ORIGIN + "/auth/google/complete"
    exchange = client.post(AUTH + "/exchange")
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["next"] == "/projects"


def test_first_login_creates_user_then_exchanges_once_without_url_tokens(oauth, verified):
    client, sessions, _ = oauth
    _, params = start(client, next_path="/projects?view=recent")
    completed = callback(client, params["state"][0])
    assert completed.status_code in {302, 303}
    assert completed.headers["location"] == ORIGIN + "/auth/google/complete"
    cookies = completed.headers.get_list("set-cookie")
    exchange_cookie = next(c for c in cookies if c.startswith(EXCHANGE_COOKIE + "="))
    assert "httponly" in exchange_cookie.lower() and "secure" in exchange_cookie.lower()
    assert "samesite=strict" in exchange_cookie.lower() or "samesite=lax" in exchange_cookie.lower()
    assert "no-store" in completed.headers.get("cache-control", "")
    result = client.post(AUTH + "/exchange")
    assert result.status_code == 200, result.text
    assert result.json()["next"] == "/projects?view=recent"
    assert result.json()["linked"] is False
    assert "no-store" in result.headers.get("cache-control", "")
    claims = decode_access_token(result.json()["access_token"])
    with sessions() as db:
        users = list(db.scalars(select(User)))
        assert len(users) == 1
        assert claims["sub"] == str(users[0].id)
        assert users[0].email == verified[0].email and users[0].password_hash is None
        assert not users[0].is_platform_admin and users[0].auth_version == 0
        identity = db.scalar(select(UserIdentity))
        assert identity.subject == verified[0].subject and identity.user_id == users[0].id
    verification = verified[1][0]
    assert verification["nonce"] == params["nonce"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verification["verifier"].encode()).digest())
    assert challenge.decode().rstrip("=") == params["code_challenge"][0]
    assert client.post(AUTH + "/exchange").status_code >= 400
    assert error_code(callback(client, params["state"][0])) == "google_state_invalid"


def test_registration_flag_does_not_block_previously_linked_login(oauth, verified, monkeypatch):
    client, sessions, _ = oauth
    monkeypatch.setattr(settings, "google_registration_enabled", False)
    _, params = start(client)
    assert error_code(callback(client, params["state"][0])) == "google_registration_disabled"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
    user_id = add_user(sessions, subject=verified[0].subject)
    _, params = start(client)
    assert (
        callback(client, params["state"][0]).headers["location"] == ORIGIN + "/auth/google/complete"
    )
    token = client.post(AUTH + "/exchange").json()["access_token"]
    assert decode_access_token(token)["sub"] == str(user_id)


def test_matching_email_requires_explicit_binding(oauth, verified):
    client, sessions, _ = oauth
    user_id = add_user(sessions, email=verified[0].email.upper())
    _, params = start(client)
    assert error_code(callback(client, params["state"][0])) == "google_email_conflict"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(UserIdentity)) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.get(User, user_id).password_hash == "not-a-real-password-hash"


def test_disabled_linked_account_cannot_login(oauth, verified):
    client, sessions, _ = oauth
    add_user(sessions, subject=verified[0].subject, active=False)
    _, params = start(client)
    assert error_code(callback(client, params["state"][0])) == "google_account_disabled"
    assert client.post(AUTH + "/exchange").status_code >= 400


def test_link_requires_current_session_and_binds_to_its_user(oauth, verified):
    client, sessions, _ = oauth
    assert client.post(AUTH + "/start", json={"mode": "link"}).status_code == 401
    user_id = add_user(sessions)
    client.headers["Authorization"] = "Bearer " + create_access_token(str(user_id), {"ver": 0})
    _, params = start(client, mode="link")
    assert (
        callback(client, params["state"][0]).headers["location"] == ORIGIN + "/auth/google/complete"
    )
    result = client.post(AUTH + "/exchange")
    assert result.status_code == 200, result.text
    assert result.json()["linked"] is True
    assert decode_access_token(result.json()["access_token"])["sub"] == str(user_id)
    with sessions() as db:
        assert db.scalar(select(UserIdentity)).user_id == user_id
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_link_cannot_take_identity_from_another_user(oauth, verified):
    client, sessions, _ = oauth
    owner = add_user(sessions, subject=verified[0].subject)
    other = add_user(sessions, email="other@example.com")
    client.headers["Authorization"] = "Bearer " + create_access_token(str(other), {"ver": 0})
    _, params = start(client, mode="link")
    assert error_code(callback(client, params["state"][0])) == "google_identity_conflict"
    with sessions() as db:
        assert db.scalar(select(UserIdentity)).user_id == owner


def test_link_fails_when_session_version_changes_during_authorization(oauth, verified):
    client, sessions, _ = oauth
    user_id = add_user(sessions)
    client.headers["Authorization"] = "Bearer " + create_access_token(str(user_id), {"ver": 0})
    _, params = start(client, mode="link")
    with sessions.begin() as db:
        db.get(User, user_id).auth_version = 1
    assert error_code(callback(client, params["state"][0])) == "google_session_expired"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(UserIdentity)) == 0


def test_logged_out_session_cannot_complete_binding(oauth, verified):
    client, sessions, _ = oauth
    user_id = add_user(sessions)
    token = create_access_token(str(user_id), {"ver": 0})
    client.headers["Authorization"] = "Bearer " + token
    _, params = start(client, mode="link")
    with sessions.begin() as db:
        db.add(
            RevokedToken(
                token_hash=revocation_key(token), expires_at=datetime.now(UTC) + timedelta(hours=1)
            )
        )
    assert error_code(callback(client, params["state"][0])) == "google_session_expired"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(UserIdentity)) == 0


@pytest.mark.parametrize("stage", ["callback", "exchange"])
def test_binding_checks_original_session_expiry_at_each_step(oauth, verified, stage):
    client, sessions, _ = oauth
    user_id = add_user(sessions)
    token = create_access_token(str(user_id), {"ver": 0})
    client.headers["Authorization"] = "Bearer " + token
    _, params = start(client, mode="link")
    if stage == "exchange":
        response = callback(client, params["state"][0])
        assert response.headers["location"] == ORIGIN + "/auth/google/complete"
    with sessions.begin() as db:
        transaction = db.scalar(select(OAuthTransaction))
        assert transaction.payload["session_expires_at"] == decode_access_token(token)["exp"]
        transaction.payload = {**transaction.payload, "session_expires_at": 0}
    if stage == "callback":
        assert error_code(callback(client, params["state"][0])) == "google_session_expired"
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(UserIdentity)) == 0
    else:
        response = client.post(AUTH + "/exchange")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "google_session_expired"
        assert "access_token" not in response.json()


def test_exchange_rejects_cross_origin_and_expiry(oauth, verified):
    client, sessions, _ = oauth
    _, params = start(client)
    callback(client, params["state"][0])
    assert (
        client.post(AUTH + "/exchange", headers={"Origin": "https://evil.example"}).status_code
        >= 400
    )
    with sessions.begin() as db:
        db.execute(
            update(OAuthTransaction).values(expires_at=datetime.now(UTC) - timedelta(seconds=5))
        )
    assert client.post(AUTH + "/exchange").status_code >= 400


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
def test_start_requires_same_origin(oauth, origin):
    client, _, _ = oauth
    client.headers.pop("Origin", None)
    response = client.post(AUTH + "/start", json={}, headers={"Origin": origin} if origin else {})
    assert response.status_code == 403
    assert BROWSER_COOKIE not in client.cookies


def test_exchange_cookie_alone_cannot_authorize_another_browser(oauth, verified):
    client, _, _ = oauth
    _, params = start(client)
    callback(client, params["state"][0])
    ticket = client.cookies.get(EXCHANGE_COOKIE)
    client.cookies.clear()
    client.cookies.set(EXCHANGE_COOKIE, ticket, path=AUTH)
    client.cookies.set(
        BROWSER_COOKIE, "another-browser-token-of-at-least-thirty-two-characters", path=AUTH
    )
    response = client.post(AUTH + "/exchange")
    assert response.status_code >= 400
    assert response.json()["error"]["code"] == "google_exchange_invalid"


@pytest.mark.parametrize("change", ["disable", "password_changed"])
def test_exchange_rechecks_account_status_before_issuing_token(oauth, verified, change):
    client, sessions, _ = oauth
    _, params = start(client)
    callback(client, params["state"][0])
    cookies = dict(client.cookies)
    with sessions.begin() as db:
        user = db.scalar(select(User))
        if change == "disable":
            user.is_active = False
        else:
            user.auth_version += 1
    response = client.post(AUTH + "/exchange")
    assert response.status_code >= 400 and "access_token" not in response.json()
    with sessions.begin() as db:
        user = db.scalar(select(User))
        user.is_active = True
        user.auth_version = 0
    client.cookies.update(cookies)
    assert client.post(AUTH + "/exchange").json()["error"]["code"] == "google_exchange_invalid"


def test_parallel_exchange_can_succeed_only_once(oauth, verified):
    client, _, api = oauth
    _, params = start(client)
    callback(client, params["state"][0])
    cookies = dict(client.cookies)
    barrier = threading.Barrier(2)

    def exchange():
        with TestClient(api, base_url=ORIGIN) as second:
            second.cookies.update(cookies)
            barrier.wait(timeout=5)
            return second.post(AUTH + "/exchange", headers={"Origin": ORIGIN}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: exchange(), range(2)))
    assert results.count(200) == 1
    assert sum(status >= 400 for status in results) == 1


def test_parallel_callbacks_consume_state_before_verification(oauth, verified):
    client, sessions, api = oauth
    _, params = start(client)
    cookies = dict(client.cookies)
    barrier = threading.Barrier(2)

    def complete():
        with TestClient(api, base_url=ORIGIN, follow_redirects=False) as second:
            second.cookies.update(cookies)
            barrier.wait(timeout=5)
            return callback(second, params["state"][0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: complete(), range(2)))
    success = ORIGIN + "/auth/google/complete"
    assert sum(response.headers["location"] == success for response in results) == 1
    rejected = next(response for response in results if response.headers["location"] != success)
    assert error_code(rejected) == "google_state_invalid"
    assert len(verified[1]) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_provider_failure_does_not_resurrect_consumed_state(oauth, monkeypatch):
    client, _, _ = oauth

    def refuse(**kwargs):
        raise google.GoogleAuthError("google_network_error", 502)

    monkeypatch.setattr(google, "verify_google_code", refuse)
    _, params = start(client)
    cookies = dict(client.cookies)
    assert error_code(callback(client, params["state"][0])) == "google_network_error"
    client.cookies.update(cookies)
    assert error_code(callback(client, params["state"][0])) == "google_state_invalid"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def mock_google(monkeypatch, key, claims, *, token_status=200, timeout=False, token=None):
    token = token or jwt.encode(claims, key, algorithm="RS256", headers={"kid": "local-key"})
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="local-key", use="sig", alg="RS256")
    requests = []

    def respond(request):
        requests.append(request)
        if timeout:
            raise httpx.ReadTimeout("local test timeout", request=request)
        if request.url.path.endswith("/token"):
            return httpx.Response(token_status, json={"id_token": token})
        return httpx.Response(200, json={"keys": [jwk]})

    client_class = httpx.Client
    monkeypatch.setattr(
        google.httpx, "Client", lambda **kw: client_class(transport=httpx.MockTransport(respond))
    )
    return requests


def claims():
    now = int(time.time())
    return {
        "iss": "https://accounts.google.com",
        "aud": "test-client.apps.googleusercontent.com",
        "sub": "local-signed-subject",
        "iat": now,
        "exp": now + 3600,
        "nonce": "expected-nonce",
        "email": "signed@example.com",
        "email_verified": True,
        "name": "Local Signing Test",
    }


def test_real_jwt_verification_and_token_request_bindings(
    google_settings, signing_key, monkeypatch
):
    requests = mock_google(monkeypatch, signing_key, claims())
    identity = google.verify_google_code(
        code="local-code", verifier="local-verifier", nonce="expected-nonce"
    )
    assert identity.subject == "local-signed-subject"
    assert identity.email == "signed@example.com" and identity.email_verified is True
    post = next(request for request in requests if request.method == "POST")
    assert str(post.url) == "https://oauth2.googleapis.com/token"
    form = parse_qs(post.content.decode())
    assert form["code_verifier"] == ["local-verifier"]
    assert form["code"] == ["local-code"]
    assert form["redirect_uri"] == [CALLBACK]
    assert form["client_secret"] == ["local-test-google-secret"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aud", "somebody-elses-client"),
        ("iss", "https://evil.example"),
        ("exp", 1),
        ("nonce", "different-nonce"),
        ("sub", ""),
        ("email_verified", False),
    ],
)
def test_rejects_invalid_signed_claims(google_settings, signing_key, monkeypatch, field, value):
    invalid = {**claims(), field: value}
    mock_google(monkeypatch, signing_key, invalid)
    with pytest.raises(AppError):
        google.verify_google_code(
            code="local-code", verifier="local-verifier", nonce="expected-nonce"
        )


@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "nonce", "aud", "iss"])
def test_required_claim_cannot_be_omitted(google_settings, signing_key, monkeypatch, claim):
    invalid = claims()
    del invalid[claim]
    mock_google(monkeypatch, signing_key, invalid)
    with pytest.raises(google.GoogleAuthError, match="Google") as error:
        google.verify_google_code(
            code="local-code", verifier="local-verifier", nonce="expected-nonce"
        )
    assert error.value.code == "google_token_invalid"


@pytest.mark.parametrize("attack", ["wrong_key", "hs256", "unsigned"])
def test_signature_and_algorithm_are_enforced(google_settings, signing_key, monkeypatch, attack):
    if attack == "wrong_key":
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(claims(), wrong_key, algorithm="RS256", headers={"kid": "local-key"})
    elif attack == "hs256":
        token = jwt.encode(
            claims(),
            "attacker-controlled-key-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": "local-key"},
        )
    else:
        token = jwt.encode(claims(), None, algorithm="none", headers={"kid": "local-key"})
    mock_google(monkeypatch, signing_key, claims(), token=token)
    with pytest.raises(google.GoogleAuthError) as error:
        google.verify_google_code(
            code="local-code", verifier="local-verifier", nonce="expected-nonce"
        )
    assert error.value.code == "google_token_invalid"


@pytest.mark.parametrize("failure", ["denied", "timeout"])
def test_upstream_failures_are_controlled_errors(
    google_settings, signing_key, monkeypatch, failure
):
    mock_google(
        monkeypatch,
        signing_key,
        claims(),
        token_status=400 if failure == "denied" else 200,
        timeout=failure == "timeout",
    )
    with pytest.raises(AppError):
        google.verify_google_code(
            code="local-code", verifier="local-verifier", nonce="expected-nonce"
        )
