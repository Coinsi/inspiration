"""Readiness checks. Never return connection strings, credentials or exception text."""

import tempfile
import urllib.request
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.core.config import settings


def database_ready():
    engine = create_engine(
        settings.database_url, poolclass=NullPool, connect_args={"connect_timeout": 3}
    )
    try:
        with engine.connect() as db:
            db.execute(text("SET statement_timeout = '3000ms'"))
            versions = set(db.scalars(text("SELECT version_num FROM alembic_version")))
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
        )
        return versions == set(ScriptDirectory.from_config(config).get_heads())
    finally:
        engine.dispose()


def storage_ready():
    if settings.storage_backend == "fs":
        root = Path(settings.fs_storage_dir)
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root) as stream:
            stream.write(b"ready")
        return True
    from app.storage.cas import _minio

    return _minio().bucket_exists(settings.minio_bucket)


def queue_ready():
    from redis import Redis

    with Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as client:
        return client.ping()


def worker_ready():
    from app.tasks.celery_app import celery_app

    return bool(celery_app.control.ping(timeout=2))


def service_ready(url, token=None):
    request = urllib.request.Request(
        url.rstrip("/") + "/health", headers={"Authorization": f"Bearer {token}"} if token else {}
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status == 200


def readiness():
    checks = {"database": database_ready, "storage": storage_ready}
    if settings.generation_executor == "celery" and not settings.celery_eager:
        checks["queue"] = queue_ready
        checks["generation_worker"] = worker_ready
    results = {}
    for name, check in checks.items():
        # Don't attempt broker inspection when Redis is already unavailable.
        if name == "generation_worker" and results.get("queue") != "ok":
            results[name] = "unavailable"
            continue
        try:
            results[name] = "ok" if check() else "unavailable"
        except Exception:
            results[name] = "unavailable"
    optional = {}
    for name, url, token in (
        ("transcriber", settings.transcriber_url, settings.transcriber_token),
        ("indexer", settings.library_indexer_url, settings.library_indexer_token),
    ):
        if not url:
            optional[name] = "not_configured"
            continue
        try:
            optional[name] = "ok" if service_ready(url, token) else "unavailable"
        except Exception:
            optional[name] = "unavailable"
    ok = all(v == "ok" for v in results.values())
    return {"status": "ok" if ok else "unavailable", "checks": results, "optional": optional}
