"""Editable material metadata must not change immutable sources or references."""

# ruff: noqa: F811
import json
import uuid

import pytest
from sqlalchemy import select

from app.models.generation import Generation
from app.models.identity import Membership
from app.models.library import MediaUsage, MediaVersion
from tests.test_material_catalog import seed
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_narrative_covers import image_bytes


def save(client, url, *, revision=1, **kwargs):
    return client.post(
        url,
        data={
            "metadata": json.dumps(
                dict(
                    name="新名称",
                    description="雨夜街道的空镜",
                    tags=[" 城市 ", "城市", "夜景"],
                    revision=revision,
                    **kwargs,
                ),
                ensure_ascii=False,
            )
        },
    )


@pytest.mark.parametrize("source", ["upload", "generated"])
def test_metadata_search_conflict_and_immutable_source(studio, source):
    client, sessions, ids, root, g, media, version = seed(studio)
    item = media if source == "upload" else g["id"]
    url = f"{root}/library/materials/{source}/{item}/profile"
    with sessions() as db:
        db.add(
            MediaUsage(
                version_id=version,
                shot_id=ids["shot"],
                purpose="visual_reference",
                start_ms=0,
                end_ms=1000,
            )
        )
        db.commit()
    response = save(client, url)
    assert response.status_code == 200, response.text
    assert response.json()["tags"] == ["城市", "夜景"]
    assert response.json()["metadata_revision"] == 2
    for word in ["新名称", "雨夜街道", "夜景"]:
        result = client.get(root + "/library/materials", params={"source": source, "query": word})
        assert result.status_code == 200, result.text
        assert result.json()["items"][0]["id"] == item
    detail = client.get(
        root
        + (f"/library/items/{media}" if source == "upload" else f"/library/generated/{g['id']}")
    ).json()
    assert detail["description"] == "雨夜街道的空镜" and detail["metadata_revision"] == 2
    assert detail["name" if source == "upload" else "title"] == "新名称"
    assert save(client, url).status_code == 409
    with sessions() as db:
        v = db.get(MediaVersion, uuid.UUID(version))
        assert v.filename == "test.mp4"
        assert db.scalar(select(MediaUsage).where(MediaUsage.version_id == version)) is not None
        assert db.get(Generation, uuid.UUID(g["id"])).prompt_snapshot == g["prompt"]
    cleared = client.post(
        url,
        data={"metadata": json.dumps(dict(name="另一个名字", description="", tags=[], revision=2))},
    )
    assert cleared.status_code == 200 and cleared.json()["tags"] == []
    assert (
        client.get(root + "/library/materials", params={"source": source, "query": "夜景"}).json()[
            "total"
        ]
        == 0
    )


@pytest.mark.parametrize("source", ["upload", "generated"])
def test_validation_permissions_and_deleted(studio, source):
    client, sessions, ids, root, g, media, version = seed(studio)
    item = media if source == "upload" else g["id"]
    url = f"{root}/library/materials/{source}/{item}/profile"
    for bad in [
        dict(name=" "),
        dict(tags=["x" * 41]),
        dict(tags=["x"] * 21),
        dict(description="x" * 4001),
    ]:
        data = dict(name="valid", tags=[], revision=1)
        data.update(bad)
        assert client.post(url, data={"metadata": json.dumps(data)}).status_code == 422
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    assert save(client, url.replace(str(ids["project"]), str(ids["other"]))).status_code == 404
    assert client.post(url.replace("/profile", "/trash")).status_code == 200
    assert save(client, url).status_code == 404
    assert client.post(url.replace("/profile", "/restore")).status_code == 200
    with sessions() as db:
        db.scalar(select(Membership).where(Membership.project_id == ids["project"])).role = "viewer"
        db.commit()
    assert save(client, url).status_code == 403


@pytest.mark.parametrize("source", ["upload", "generated"])
def test_video_cover_atomic_and_scoped(studio, source):
    client, sessions, ids, root, g, media, version = seed(studio)
    with sessions() as db:
        db.get(Generation, uuid.UUID(g["id"])).output_type = "video"
        db.commit()
    item = media if source == "upload" else g["id"]
    url = f"{root}/library/materials/{source}/{item}/profile"
    data = {"metadata": json.dumps(dict(name="视频封面", revision=1, version_id=version))}
    assert (
        client.post(url, data=data, files={"file": ("bad.png", b"bad", "image/png")}).status_code
        == 422
    )
    raw = image_bytes()
    response = client.post(url, data=data, files={"file": ("cover.png", raw, "image/png")})
    assert response.status_code == 200, response.text
    row = client.get(root + "/library/materials", params={"source": source}).json()["items"][0]
    assert row["poster_hash"]
    assert client.get(root + "/blobs/" + row["poster_hash"]).content == raw
    assert (
        client.get(f"/api/v1/projects/{ids['other']}/blobs/{row['poster_hash']}").status_code == 403
    )
    if source == "upload":
        with sessions() as db:
            v = db.get(MediaVersion, uuid.UUID(version))
            assert v.original_hash is None and v.filename == "test.mp4"
