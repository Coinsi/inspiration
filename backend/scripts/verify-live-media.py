"""Opt-in real provider verification. Two image requests; isolated DB schema; no secret output.

Run from backend with --project-id UUID --output-dir PATH --live.
Reads the configured environment; never edits source project records or provider configuration.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=uuid.UUID, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, help="Optional existing backend environment file")
    parser.add_argument(
        "--live", action="store_true", help="Allow two potentially billable image requests"
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Real provider calls require --live")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.env_file:
        from dotenv import load_dotenv

        load_dotenv(args.env_file)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["STORAGE_BACKEND"] = "fs"
    os.environ["FS_STORAGE_DIR"] = str(out / "blobs")
    os.environ["GENERATION_EXECUTOR"] = "local"
    os.environ["CELERY_EAGER"] = "false"

    from sqlalchemy import select
    from sqlalchemy.schema import CreateSchema, DropSchema

    from app.core import database
    from app.core.security import create_access_token
    from app.models import Base
    from app.models.generation import ProviderConfig
    from app.models.identity import Membership, Project, User
    from app.models.narrative import Scene, Script
    from app.models.shot import Shot

    with database.SessionLocal() as db:
        provider = db.scalar(
            select(ProviderConfig).where(
                ProviderConfig.project_id == args.project_id,
                ProviderConfig.provider_name == "gpt_image",
                ProviderConfig.enabled.is_(True),
            )
        )
        if not provider or not provider.credentials_encrypted:
            raise RuntimeError("Source project has no enabled GPT Image credentials")
        provider_values = {
            key: getattr(provider, key)
            for key in (
                "provider_name",
                "kind",
                "enabled",
                "endpoint",
                "credentials_encrypted",
                "config",
                "capabilities",
            )
        }
    schema = "test_live_media_" + uuid.uuid4().hex
    with database.engine.begin() as conn:
        conn.execute(CreateSchema(schema))
    scoped = database.engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(scoped, checkfirst=False)
    database.SessionLocal.configure(bind=scoped)
    report = {
        "schema": schema,
        "model": provider_values["config"].get("model"),
        "checks": [],
        "status": "running",
    }

    def record(name, **data):
        report["checks"].append({"name": name, **data})
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"check": name, **data}, ensure_ascii=False), flush=True)

    from fastapi.testclient import TestClient

    from app.main import app
    from app.modules.generation.jobs import pool

    try:
        with database.SessionLocal() as db:
            user = User(
                username="live_media_test",
                email="verification@example.com",
                password_hash="unused",
                display_name="Live verification",
            )
            db.add(user)
            db.flush()
            project = Project(code="LIVE-TEST", name="Isolated live verification", owner_id=user.id)
            db.add(project)
            db.flush()
            db.add(Membership(project_id=project.id, user_id=user.id, role="admin"))
            db.add(ProviderConfig(project_id=project.id, **provider_values))
            script = Script(project_id=project.id, code="SCR-TEST", title="Verification")
            db.add(script)
            db.flush()
            scene = Scene(
                project_id=project.id,
                script_id=script.id,
                code="SC-TEST",
                title="Verification",
                ordinal=1,
            )
            db.add(scene)
            db.flush()
            shot = Shot(
                project_id=project.id,
                scene_id=scene.id,
                code="SH-TEST",
                title="Live media verification",
                ordinal=1,
            )
            db.add(shot)
            db.flush()
            db.commit()
            project_id, shot_id = str(project.id), str(shot.id)
            token = create_access_token(str(user.id))
        with TestClient(app) as client:
            client.headers["Authorization"] = "Bearer " + token
            base = f"/api/v1/projects/{project_id}"

            def request(method, path, data=None):
                response = client.request(method, base + path, json=data)
                if response.status_code != 200:
                    raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
                return response.json()

            def wait_job(job, name):
                started = time.monotonic()
                previous = None
                while time.monotonic() - started < 720:
                    job = request("GET", "/jobs/" + job["id"])
                    if job["status"] != previous:
                        record(name, job_id=job["id"], status=job["status"])
                        previous = job["status"]
                    if job["status"] in {"succeeded", "failed", "canceled"}:
                        if job["status"] != "succeeded":
                            raise RuntimeError(f"{name}: {job.get('error') or job['status']}")
                        record(
                            name + " completed",
                            seconds=round(time.monotonic() - started, 2),
                            recorded_points=job["actual_cost"],
                        )
                        return job
                    time.sleep(1.5)
                raise RuntimeError(name + " timeout; not resubmitted")

            def result_for(job, target_type, target_id, filename):
                results = request(
                    "GET", f"/generations?target_type={target_type}&target_id={target_id}"
                )
                result = next(g for g in results if g["job_id"] == job["id"])
                response = client.get(base + "/blobs/" + result["output_blob_hash"])
                assert response.status_code == 200
                (out / filename).write_bytes(response.content)
                record(
                    filename,
                    bytes=len(response.content),
                    mime=response.headers.get("content-type"),
                    sha256=result["output_blob_hash"],
                )
                return result

            common = {
                "provider": "gpt_image",
                "request_type": "image",
                "count": 1,
                "use_references": False,
                "provider_params": {"size": "1024x1024", "quality": "low"},
            }
            create = {
                **common,
                "prompt_override": "Studio product photograph of one matte red ceramic teapot with a round body, handle on the right and spout on the left. Plain light gray background. Soft lighting, centered composition, no text, no other objects.",
            }
            estimate = request("POST", f"/shots/{shot_id}/estimate", create)
            record("estimate", points_per_request=estimate["points"], max_real_image_requests=2)
            job = wait_job(
                request("POST", f"/shots/{shot_id}/generate", create), "real image generation"
            )
            original = result_for(job, "shot", shot_id, "real-original.png")
            from PIL import Image

            with Image.open(out / "real-original.png") as image:
                image.load()
                record("original decoded", width=image.width, height=image.height)
                if image.size != (1024, 1024):
                    report.setdefault("limitations", []).append(
                        f"Requested 1024x1024, provider returned {image.width}x{image.height}"
                    )
                    record(
                        "requested image dimensions",
                        status="failed",
                        requested=[1024, 1024],
                        actual=list(image.size),
                    )
            edit = {
                **common,
                "source_generation_id": original["id"],
                "prompt_override": "Edit the supplied photo. Change the teapot body, handle and spout from red to cobalt blue. Keep exactly the same shape, composition, plain light gray background and lighting. No text or additional objects.",
            }
            job = wait_job(
                request("POST", f"/shots/{shot_id}/generate", edit), "real reference edit"
            )
            edited = result_for(job, "shot", shot_id, "real-edited.png")
            assert edited["output_blob_hash"] != original["output_blob_hash"]
            request("POST", "/generations/" + edited["id"] + "/select")
            local = wait_job(
                request(
                    "POST",
                    "/generations/" + edited["id"] + "/refine",
                    {"crop": [0.1, 0.1, 0.8, 0.8], "brightness": 1.1},
                ),
                "local refinement of real output",
            )
            refined = result_for(local, "shot", shot_id, "real-refined.png")
            timeline = request("POST", "/timelines", {"name": "Real output assembly"})
            request(
                "PUT",
                f"/timelines/{timeline['id']}/items",
                {
                    "items": [
                        {"shot_id": shot_id, "generation_id": original["id"], "duration_ms": 1500},
                        {"shot_id": shot_id, "generation_id": refined["id"], "duration_ms": 1500},
                    ]
                },
            )
            film_job = wait_job(
                request("POST", f"/timelines/{timeline['id']}/render", {"height": 720}),
                "real outputs to MP4",
            )
            film = result_for(film_job, "timeline", timeline["id"], "real-output-film.mp4")
            partial = client.get(
                base + "/blobs/" + film["output_blob_hash"], headers={"Range": "bytes=0-31"}
            )
            assert partial.status_code == 206 and len(partial.content) == 32
            record("film range playback", status=206)
            report["status"] = "passed_with_limitations" if report.get("limitations") else "passed"
    except Exception as exc:
        report["status"] = "failed"
        record("failure", error=str(exc)[:1000])
    finally:
        pool.shutdown(wait=True)
        with database.engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        report["schema_removed"] = True
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return (
        0
        if report["status"] == "passed"
        else 2
        if report["status"] == "passed_with_limitations"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
