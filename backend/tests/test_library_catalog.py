"""Bounded catalog, literal-name search and off-page version lookup."""

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime

from app.models.library import LibraryMedia, MediaVersion
from tests.test_media_workflow import studio  # noqa: F401


def test_catalog_pages_literal_search_and_project_boundaries(studio):
    client, sessions, ids = studio
    with sessions() as db:
        rows = []
        for i in range(55):
            item = LibraryMedia(project_id=uuid.UUID(ids["project"]), name=f"素材 {i:03d}")
            db.add(item)
            db.flush()
            rows.append(str(item.id))
            db.add(
                MediaVersion(
                    media_id=item.id,
                    ordinal=1,
                    filename="clip.mp4",
                    fingerprint="a" * 64,
                    size_bytes=100,
                    status="ready",
                )
            )
        item = LibraryMedia(
            project_id=uuid.UUID(ids["project"]), name="100%_原片", deleted_at=datetime.now(UTC)
        )
        db.add(item)
        db.flush()
        trash = str(item.id)
        other = LibraryMedia(project_id=ids["other"], name="别的项目")
        db.add(other)
        db.flush()
        other_id = str(other.id)
        db.commit()
    root = f"/api/v1/projects/{ids['project']}/library"
    pages = [client.get(root + f"/catalog?sort=name&offset={n * 24}").json() for n in range(3)]
    assert [len(p["items"]) for p in pages] == [24, 24, 7]
    assert all(p["total"] == 55 for p in pages)
    assert len({i["id"] for page in pages for i in page["items"]}) == 55
    assert all(len(i["versions"]) == 1 for page in pages for i in page["items"])
    assert client.get(root + "/catalog", params={"query": "%"}).json()["total"] == 0
    assert (
        client.get(root + "/catalog", params={"query": "%_", "deleted": "true"}).json()["items"][0][
            "id"
        ]
        == trash
    )
    assert client.get(root + f"/items/{rows[-1]}").json()["versions"][0]["ordinal"] == 1
    assert client.get(root + f"/items/{other_id}").status_code == 404
    assert client.get(root + "/catalog?limit=100000").status_code == 422


def test_version_catalog_bounds_revisions_and_preserves_exact_lookup(studio):
    client, sessions, ids = studio
    with sessions() as db:
        item = LibraryMedia(project_id=ids["project"], name="剪辑 100%_海边")
        archived = LibraryMedia(
            project_id=ids["project"], name="回收视频", deleted_at=datetime.now(UTC)
        )
        foreign = LibraryMedia(project_id=ids["other"], name="其他项目")
        db.add_all([item, archived, foreign])
        db.flush()
        versions = []
        for ordinal in range(1, 57):
            v = MediaVersion(
                media_id=item.id,
                ordinal=ordinal,
                filename="source.mp4",
                fingerprint="b" * 64,
                size_bytes=100,
                status="ready" if ordinal <= 55 else "processing",
            )
            db.add(v)
            db.flush()
            versions.append(str(v.id))
        excluded = []
        for source in (archived, foreign):
            v = MediaVersion(
                media_id=source.id,
                ordinal=1,
                filename="source.mp4",
                fingerprint="c" * 64,
                size_bytes=100,
                status="ready",
            )
            db.add(v)
            db.flush()
            excluded.append(str(v.id))
        db.commit()
    root = f"/api/v1/projects/{ids['project']}/library"
    pages = [client.get(root + f"/version-catalog?offset={i * 24}").json() for i in range(3)]
    assert [len(p["items"]) for p in pages] == [24, 24, 7]
    assert all(p["total"] == 55 for p in pages)
    assert [v["id"] for p in pages for v in p["items"]] == list(reversed(versions[:55]))
    assert client.get(root + "/version-catalog", params={"query": "%_"}).json()["total"] == 55
    assert client.get(root + "/version-catalog", params={"query": "missing"}).json()["total"] == 0
    assert client.get(root + "/version-catalog?limit=101").status_code == 422
    exact = client.get(root + f"/versions/{versions[0]}").json()
    assert exact["name"] == "剪辑 100%_海边" and exact["ordinal"] == 1
    assert all(client.get(root + f"/versions/{v}").status_code == 404 for v in excluded)
