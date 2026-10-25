"""Configuration -> saved profile -> real SDK -> existing job/asset pipeline."""

# ruff: noqa: F811
import base64
import json
import uuid

from app.modules.assist.service import _engine
from app.modules.generation import jobs, media_engine
from app.modules.narrative.service import _strategy
from tests.test_litellm_sdk import js, upstream
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup


def test_sdk_channel_image_runs_existing_job_pipeline(studio, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    client, sessions, ids = studio
    image = media_engine.mock_image()
    with upstream(
        lambda *_: js(
            {
                "created": 1,
                "data": [
                    {"b64_json": base64.b64encode(image).decode()},
                ],
            }
        )
    ) as (endpoint, calls):
        root, cid = setup(client, ids["project"], endpoint=endpoint)
        model = add(
            client, root, cid, model="gpt-image-1", protocol="litellm", sdk_provider="openai"
        )
        monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
        response = client.post(
            root + f"/shots/{ids['shot']}/generate",
            json={
                "provider": model["provider_name"],
                "count": 1,
                "prompt_override": "draw",
            },
        )
        assert response.status_code == 200, response.text
        job = response.json()
        with sessions() as db:
            jobs.execute(db, uuid.UUID(job["id"]))
        result = client.get(root + f"/jobs/{job['id']}").json()
        assert result["status"] == "succeeded", result
        assert len(calls) == 1
        assert json.loads(calls[0][3])["model"] == "gpt-image-1"
        assert "test-secret" not in json.dumps(result)


def test_sdk_provider_survives_configuration_and_text_factories(studio):
    client, sessions, ids = studio
    root, cid = setup(client, ids["project"])
    model = add(
        client,
        root,
        cid,
        protocol="cloud_llm",
        modality="text",
        model="claude-test",
        sdk_provider="anthropic",
        is_default=True,
    )
    assert model["sdk_provider"] == "anthropic"
    with sessions() as db:
        assert _engine(db, uuid.UUID(ids["project"]), None).sdk_provider == "anthropic"
        assert _strategy(db, uuid.UUID(ids["project"]), None).sdk_provider == "anthropic"
    catalog = client.get(root + "/model-channels").json()
    assert "gemini" in catalog["protocols"]["litellm"]["sdk_providers"]["video"]


def test_sdk_handle_migration_preserves_long_operation_names(studio):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    _, sessions, _ = studio
    with sessions() as db:
        url = db.get_bind().url
    spec = importlib.util.spec_from_file_location(
        "sdk_migration",
        Path(__file__).parents[1] / "alembic/versions/0054_sdk_video_handles.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                schema = "sdk_migration_" + uuid.uuid4().hex
                conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                conn.exec_driver_sql("CREATE TABLE generation_job (external_job_id varchar(255))")
                migration.op = Operations(MigrationContext.configure(conn))
                migration.upgrade()
                handle = "video_" + "a" * 1024
                conn.execute(
                    text("INSERT INTO generation_job VALUES (:handle)"), {"handle": handle}
                )
                assert (
                    conn.execute(text("SELECT external_job_id FROM generation_job")).scalar_one()
                    == handle
                )
            finally:
                tx.rollback()
    finally:
        engine.dispose()
