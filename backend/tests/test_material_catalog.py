"""A single media catalog preserves sources, scoped visibility and reference safety."""

# ruff: noqa: F811
import uuid

from sqlalchemy import func, select

from app.models.asset import Asset
from app.models.generation import Generation
from app.models.identity import Membership
from app.models.library import LibraryMedia, MediaUsage, MediaVersion
from app.models.storage import Blob
from tests.test_media_workflow import post_ok, studio  # noqa: F401
from tests.test_workbench import body


def seed(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    post_ok(client, root + "/workbench/generate", body(prompt_override="生成图像：清晨 100%"))
    generation = client.get(root + "/library/generated").json()["items"][0]
    with sessions() as db:
        media = LibraryMedia(project_id=ids["project"], name="上传的视频 100%")
        db.add(media)
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            ordinal=1,
            filename="test.mp4",
            media_type="video",
            fingerprint="a" * 64,
            size_bytes=12,
            uploaded_bytes=12,
            status="ready",
        )
        db.add(version)
        db.flush()
        upload_id = str(media.id)
        version_id = str(version.id)
        db.commit()
    return client, sessions, ids, root, generation, upload_id, version_id


def test_catalog_unifies_sources_filters_and_paging_without_copy(studio):
    client, sessions, ids, root, g, media, version = seed(studio)
    base = root + "/library/materials"
    all_items = client.get(base).json()
    assert all_items["total"] == 2
    assert {i["source"] for i in all_items["items"]} == {"generated", "upload"}
    assert {i["id"] for i in all_items["items"]} == {g["id"], media}
    assert next(i for i in all_items["items"] if i["source"] == "generated")["is_test"] is True
    assert next(i for i in all_items["items"] if i["source"] == "upload")["is_test"] is False
    assert client.get(base + "?kind=image").json()["total"] == 1
    assert client.get(base + "?kind=audio").json()["total"] == 0
    assert client.get(base + "?source=upload").json()["items"][0]["version_id"] == version
    assert client.get(base + "?query=100%25").json()["total"] == 2
    assert client.get(base + "?query=不存在").json()["total"] == 0
    assert (
        client.get(base + "?limit=1&offset=1").json()["items"][0]["id"]
        == all_items["items"][1]["id"]
    )
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Generation)) == 1
        assert db.scalar(select(func.count()).select_from(LibraryMedia)) == 1


def test_generated_trash_restores_without_removing_source_or_blob(studio):
    client, sessions, ids, root, g, media, version = seed(studio)
    base = root + "/library/materials"
    post_ok(client, f"{base}/generated/{g['id']}/trash")
    assert client.get(base).json()["total"] == 1
    assert client.get(root + "/library/generated").json()["total"] == 0
    assert client.get(base + "?deleted=true").json()["items"][0]["id"] == g["id"]
    with sessions() as db:
        assert db.get(Generation, uuid.UUID(g["id"])) is not None
        assert db.get(Blob, g["blob_hash"]) is not None
    post_ok(client, f"{base}/generated/{g['id']}/restore")
    assert client.get(base).json()["total"] == 2
    assert client.get(root + "/library/generated").json()["total"] == 1


def test_shared_trash_includes_entities_and_preserves_media_reference_guards(studio):
    client, sessions, ids, root, g, media, version = seed(studio)
    base = root + "/library/materials"
    with sessions() as db:
        asset = Asset(project_id=ids["project"], name="测试角色", code="CAST-1", type="character")
        db.add(asset)
        db.flush()
        asset_id = str(asset.id)
        usage = MediaUsage(
            version_id=version,
            shot_id=ids["shot"],
            purpose="visual_reference",
            start_ms=0,
            end_ms=1000,
        )
        db.add(usage)
        db.commit()
    assert client.post(f"{base}/upload/{media}/trash").status_code == 409
    post_ok(client, f"{base}/asset/{asset_id}/trash")
    assert client.get(base + "?deleted=true&source=asset").json()["items"][0]["id"] == asset_id
    post_ok(client, f"{base}/asset/{asset_id}/restore")
    assert client.get(base + "?deleted=true").json()["total"] == 0


def test_catalog_and_mutations_enforce_project_and_role(studio):
    client, sessions, ids, root, g, media, version = seed(studio)
    other = f"/api/v1/projects/{ids['other']}/library/materials"
    assert client.get(other).status_code == 403
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    assert client.get(other).json()["total"] == 0
    for source, item in [("upload", media), ("generated", g["id"])]:
        assert client.post(f"{other}/{source}/{item}/trash").status_code == 404
    with sessions() as db:
        member = db.scalar(select(Membership).where(Membership.project_id == ids["project"]))
        member.role = "viewer"
        db.commit()
    assert client.get(root + "/library/materials").status_code == 200
    assert client.post(f"{root}/library/materials/generated/{g['id']}/trash").status_code == 403


def test_latest_version_is_single_row_and_upload_restore(studio):
    client, sessions, ids, root, g, media, version = seed(studio)
    with sessions() as db:
        next_version = MediaVersion(
            media_id=media,
            ordinal=2,
            filename="next.mp4",
            fingerprint="b" * 64,
            size_bytes=24,
            uploaded_bytes=24,
            media_type="video",
            status="ready",
        )
        db.add(next_version)
        db.flush()
        next_id = str(next_version.id)
        db.commit()
    base = root + "/library/materials"
    rows = client.get(base + "?source=upload").json()
    assert rows["total"] == 1 and rows["items"][0]["version_id"] == next_id
    post_ok(client, f"{base}/upload/{media}/trash")
    assert client.get(base + "?source=upload").json()["total"] == 0
    assert client.get(base + "?deleted=true&source=upload").json()["total"] == 1
    post_ok(client, f"{base}/upload/{media}/restore")
    assert client.get(base + "?source=upload").json()["total"] == 1
    with sessions() as db:
        assert db.get(MediaVersion, uuid.UUID(version)) is not None
