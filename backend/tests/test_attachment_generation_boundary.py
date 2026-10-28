"""Independent files cross real provider adapters with only network I/O simulated."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import func, select

from app.adapters.contracts import GenerationResult, Output
from app.adapters.generation.video_v1 import VideoV1Provider
from app.core.config import settings
from app.models.attachment import Attachment
from app.models.generation import Generation, GenerationJob
from app.modules.generation import jobs
from app.modules.harness import media_tools
from app.modules.tools import capabilities, registry
from app.modules.tools.invocation import invoke
from tests.test_attachment_model_context import seed
from tests.test_harness_context import context as project_context
from tests.test_harness_media_tools import args, context, setup_run
from tests.test_media_workflow import changing_video, studio  # noqa: F401
from tests.test_model_channels import add, setup


@pytest.fixture(autouse=True)
def tool_registry(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    media_tools.register_tools()


def stage(studio, changing_video, monkeypatch, kind):
    client, sessions, ids = studio
    monkeypatch.setattr(
        settings, "generation_reference_public_base_url", "https://studio.example.com"
    )
    root, channel = setup(client, ids["project"])
    profile = add(
        client,
        root,
        channel,
        name="Attachment video",
        model="fixture-video",
        protocol="video_v1",
        modality="video",
        max_reference_images=1,
        max_reference_videos=1,
        parameters={"duration": [4], "resolution": ["720p"]},
    )

    def persist(db, job):
        db.commit()
        return job

    monkeypatch.setattr(jobs, "dispatch", persist)
    rid, tasks = setup_run(sessions, ids)
    with sessions() as db:
        item = (
            seed(db, ids, changing_video, "video", duration_ms=2000)
            if kind == "video"
            else seed(db, ids)
        )
        db.commit()
        receipt = invoke(
            db,
            project_context(db, ids),
            context(rid, tasks[0]),
            "media.generate",
            args(
                provider=profile["provider_name"],
                request_type="video",
                provider_params={"duration": 4, "resolution": "720p"},
                references=[{"attachment_id": item["id"]}],
            ),
            allow_charges=True,
        )
        assert receipt.status == "succeeded", receipt.model_dump()
        return sessions, ids, item, uuid.UUID(receipt.data["job_id"])


@pytest.mark.parametrize("kind", ["image", "video"])
def test_attachment_reference_reaches_video_v1_wire_and_scoped_download(
    studio, changing_video, monkeypatch, kind
):
    sessions, ids, item, jid = stage(studio, changing_video, monkeypatch, kind)
    calls = []

    def request(provider, method, path, **kwargs):
        assert method == "POST" and path == "/videos"
        calls.append(kwargs["json"])
        return {"id": "simulated-attachment-job"}

    monkeypatch.setattr(VideoV1Provider, "_request", request)
    monkeypatch.setattr(
        VideoV1Provider,
        "poll",
        lambda *a: GenerationResult(
            status="succeeded", outputs=[Output(type="video", data=changing_video)]
        ),
    )
    with sessions() as db:
        jobs.execute(db, jid)
        job = db.get(GenerationJob, jid)
        assert job.status == "succeeded", job.error
        assert job.input_snapshot["reference_sources"] == [
            {"attachment_id": item["id"], "role": "reference"}
        ]
        assert "base64" not in str(job.input_snapshot)
        assert (
            db.scalar(select(func.count()).select_from(Generation).where(Generation.job_id == jid))
            == 1
        )
    assert len(calls) == 1 and calls[0]["operation"] == kind + "_to_video"
    source = calls[0]["media_inputs"][0]
    assert source["kind"] == kind and source["role"] == "reference"
    assert "/generation-references/" + item["blob_hash"] in source["url"]
    response = studio[0].get(source["url"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(kind + "/")
    if kind == "video":
        assert response.content == changing_video


@pytest.mark.parametrize("change", ["owner", "status", "hash"])
def test_attachment_change_before_dispatch_prevents_upstream_call(
    studio, changing_video, monkeypatch, change
):
    sessions, ids, item, jid = stage(studio, changing_video, monkeypatch, "image")
    with sessions() as db:
        row = db.get(Attachment, uuid.UUID(item["id"]))
        if change == "owner":
            row.created_by = uuid.uuid4()
        elif change == "status":
            row.status = "cancelled"
        else:
            other = seed(db, ids, changing_video, "video", duration_ms=2000)
            row.blob_hash = other["blob_hash"]
        db.commit()
    monkeypatch.setattr(
        VideoV1Provider, "_request", lambda *a, **kw: pytest.fail("revoked source sent upstream")
    )
    with sessions() as db:
        jobs.execute(db, jid)
        job = db.get(GenerationJob, jid)
        assert job.status == "failed" and job.external_job_id is None
        assert (
            db.scalar(select(func.count()).select_from(Generation).where(Generation.job_id == jid))
            == 0
        )
