"""Publication boundaries, revisions, search and public cover access."""

# ruff: noqa: F811
import io

from PIL import Image

from tests.test_media_workflow import studio  # noqa: F401
from tests.test_website import administrator


def content(**changes):
    return {
        "title": "从故事开始",
        "excerpt": "第一篇创作指南",
        "body": "## 故事起点\n\n先从人物想要什么开始，保留可以被镜头表现出来的变化。",
        "category": "创作指南",
        "author": "编辑部",
        "cover_media_id": None,
        **changes,
    }


def create(client, slug="first-note", **changes):
    r = client.post("/api/v1/admin/blog", json={"slug": slug, "content": content(**changes)})
    assert r.status_code == 201, r.text
    return r.json()


def action(client, post, action):
    r = client.post(f"/api/v1/admin/blog/{post['id']}/{action}", json={"version": post["version"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_blog_requires_platform_role(studio):
    client, _, _ = studio
    assert client.get("/api/v1/blog").json() == {"items": [], "total": 0}
    assert client.get("/api/v1/admin/blog").status_code == 403
    assert (
        client.post("/api/v1/admin/blog", json={"slug": "note", "content": content()}).status_code
        == 403
    )
    administrator(studio)
    post = create(client)
    client.headers.pop("Authorization")
    assert client.get(f"/api/v1/admin/blog/{post['id']}").status_code == 401
    assert client.get("/api/v1/blog/first-note").status_code == 404


def test_blog_draft_publish_search_archive(studio):
    client = administrator(studio)
    post = create(client)
    assert client.get("/api/v1/blog").json()["total"] == 0
    post = action(client, post, "publish")
    assert client.get("/api/v1/blog?q=镜头").json()["total"] == 1
    assert client.get("/api/v1/blog?category=产品更新").json()["total"] == 0
    assert "body" not in client.get("/api/v1/blog").json()["items"][0]
    r = client.put(
        f"/api/v1/admin/blog/{post['id']}",
        json={"version": post["version"], "content": content(title="秘密草稿")},
    )
    newer = r.json()
    assert client.get("/api/v1/blog/first-note").json()["title"] == "从故事开始"
    assert client.get("/api/v1/blog?q=秘密").json()["total"] == 0
    assert (
        client.post(
            f"/api/v1/admin/blog/{post['id']}/publish", json={"version": post["version"]}
        ).status_code
        == 409
    )
    newer = action(client, newer, "publish")
    assert client.get("/api/v1/blog/first-note").json()["title"] == "秘密草稿"
    newer = action(client, newer, "archive")
    assert client.get("/api/v1/blog/first-note").status_code == 404
    assert client.get("/api/v1/admin/blog").json()["total"] == 0
    assert client.get("/api/v1/admin/blog?archived=true").json()["total"] == 1
    newer = action(client, newer, "restore")
    assert client.get("/api/v1/blog").json()["total"] == 0
    newer = action(client, newer, "publish")
    assert client.get("/api/v1/blog").json()["total"] == 1
    action(client, newer, "unpublish")
    assert client.get("/api/v1/blog").json()["total"] == 0


def test_blog_slug_and_publish_validation(studio):
    client = administrator(studio)
    post = create(client, body="", excerpt="")
    assert (
        client.post(
            f"/api/v1/admin/blog/{post['id']}/publish", json={"version": post["version"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/admin/blog", json={"slug": "first-note", "content": content()}
        ).status_code
        == 409
    )
    for slug in ("../admin", "UPPER", "has spaces", "https://evil.com"):
        assert (
            client.post("/api/v1/admin/blog", json={"slug": slug, "content": content()}).status_code
            == 422
        )
    assert client.get("/api/v1/blog?q=%25").json()["total"] == 0


def test_blog_cover_withdrawal_and_shared_reference(studio):
    client = administrator(studio)
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "green").save(data, "PNG")
    media = client.post(
        "/api/v1/admin/website/media", files={"file": ("cover.png", data.getvalue(), "image/png")}
    ).json()
    mid = media["id"]
    url = f"/api/v1/website/media/{mid}"
    a = create(client, cover_media_id=mid)
    assert client.get(url).status_code == 404
    a = action(client, a, "publish")
    assert client.get(url).status_code == 200
    b = action(client, create(client, slug="second-note", cover_media_id=mid), "publish")
    action(client, a, "unpublish")
    assert client.get(url).status_code == 200
    action(client, b, "archive")
    assert client.get(url).status_code == 404
