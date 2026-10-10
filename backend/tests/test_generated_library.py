"""Generated media is a scoped, durable collection, not a duplicated upload."""

# ruff: noqa: F811
import uuid

from sqlalchemy import func, select

from app.adapters.contracts import Capabilities
from app.adapters.generation.mock import MockProvider
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership
from app.models.storage import Blob
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_workbench import body


def test_auto_collection_history_paging_and_reference(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    request = body(prompt_override="图书馆的清晨 100%")
    job = client.post(root + "/workbench/generate", json=request).json()
    assert job["status"] == "succeeded"
    # Collection is read later, as for historical results; no upload/backfill needed.
    with sessions() as db:
        blob_count = db.scalar(select(func.count()).select_from(Blob))
    result = client.get(root + "/library/generated").json()
    assert result["total"] == 1
    item = result["items"][0]
    assert item["job_id"] == job["id"]
    assert item["prompt"] == request["prompt_override"]
    assert item["reference_available"]
    assert client.get(root + f"/library/generated/{item['id']}").json() == item
    assert client.get(root + "/library/generated?query=100%25").json()["total"] == 1
    assert client.get(root + "/library/generated?kind=video").json()["total"] == 0
    assert client.get(root + "/library/generated?offset=1&limit=1").json()["items"] == []
    assert client.get(root + "/library/generated?limit=0").status_code == 422
    assert client.post(root + "/workbench/generate", json=request).json()["id"] == job["id"]
    assert client.get(root + "/library/generated").json()["total"] == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Blob)) == blob_count
    monkeypatch.setattr(
        MockProvider,
        "capabilities",
        lambda _: Capabilities(
            modalities={"image"}, features={"reference"}, max_reference_images=2
        ),
    )
    reused = client.post(
        root + "/workbench/generate",
        json=body(references=[{"generation_id": item["id"], "role": "reference"}]),
    )
    assert reused.status_code == 200, reused.text
    assert reused.json()["input_snapshot"]["references"][0]["blob_hash"] == item["blob_hash"]


def test_collection_filters_success_and_project_boundaries(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    job = client.post(root + "/workbench/generate", json=body()).json()
    item = client.get(root + "/library/generated").json()["items"][0]
    other = f"/api/v1/projects/{ids['other']}"
    assert client.get(other + "/library/generated").status_code == 403
    assert client.get(other + f"/library/generated/{item['id']}").status_code == 403
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="viewer"))
        db.get(GenerationJob, uuid.UUID(job["id"])).status = "failed"
        db.commit()
    assert client.get(root + "/library/generated").json()["total"] == 0
    assert client.get(other + f"/library/generated/{item['id']}").status_code == 404
    with sessions() as db:
        db.get(GenerationJob, uuid.UUID(job["id"])).status = "succeeded"
        db.get(Generation, uuid.UUID(item["id"])).output_type = "video"
        db.commit()
    assert client.get(root + "/library/generated?kind=image").json()["total"] == 0
    assert client.get(root + "/library/generated?kind=video").json()["total"] == 1
    with sessions() as db:
        db.get(Generation, uuid.UUID(item["id"])).output_blob_hash = None
        db.commit()
    assert client.get(root + "/library/generated").json()["total"] == 0
