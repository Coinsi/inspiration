"""Original story flows: project isolation, inherited chapter context and safe script saves."""

# ruff: noqa: F811
import uuid

from app.models.identity import Membership
from app.models.narrative import Chapter, Novel, Script
from app.modules.narrative import service
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_import_adapt_setting_scene_link_and_revision(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    imported = client.post(
        root + "/novels/import",
        files={
            "file": (
                "海边.txt",
                "第一章海边\n林舟推开窗户。\n第二章归途\n海边灯塔亮了。".encode("utf-8-sig"),
                "text/plain",
            )
        },
    )
    assert imported.status_code == 200, imported.text
    novel = client.get(root + "/novels/" + imported.json()["id"]).json()
    assert len(novel["chapters"]) == 2 and novel["chapters"][0]["title"] == "第一章海边"
    chapter = novel["chapters"][0]["id"]
    post_ok(
        client,
        root + "/settings",
        {
            "novel_id": novel["id"],
            "category": "world",
            "name": "海边小镇",
            "content": "故事发生在秋天。",
        },
    )
    from app.adapters.decomposition.mock import MockDecomposer

    class Adapter(MockDecomposer):
        def adapt_chapter_to_script(self, text, context):
            assert "秋天" in context and "林舟" in text
            return [
                {"block_type": "scene_heading", "text": "内景 海边小屋 清晨"},
                {"block_type": "action", "text": "林舟推开窗户。"},
                {"block_type": "dialogue", "text": "今天一起看海。"},
            ]

    monkeypatch.setattr(service, "_strategy", lambda *_: Adapter())
    script = post_ok(client, root + "/scripts/from-chapter", {"chapter_id": chapter})
    assert script["source_chapter_id"] == chapter and len(script["content_revision"]) == 64
    suggestion = post_ok(client, root + f"/scripts/{script['id']}/decompose")
    scenes = post_ok(
        client, root + f"/scripts/{script['id']}/apply-scenes", {"scenes": suggestion["scenes"]}
    )
    assert scenes[0]["adapted_from_chapter_id"] == chapter
    scoped = client.get(root + "/shots", params={"chapter_id": chapter}).json()
    assert len(scoped) >= 1 and all(s["novel_id"] == novel["id"] for s in scoped)
    character = post_ok(
        client,
        root + "/settings",
        {
            "novel_id": novel["id"],
            "category": "character",
            "name": "林舟",
            "content": "穿蓝色外套。",
        },
    )
    asset = post_ok(client, root + f"/settings/{character['id']}/to-asset")
    assert asset["name"] == "林舟"
    changed = [{**b, "text": b["text"] + " 新稿"} for b in script["content_blocks"]]
    update = client.put(
        root + f"/scripts/{script['id']}/blocks",
        json={"blocks": changed, "expected_revision": script["content_revision"]},
    )
    assert update.status_code == 200, update.text
    assert update.json()["content_revision"] != script["content_revision"]
    assert (
        client.put(
            root + f"/scripts/{script['id']}/blocks",
            json={
                "blocks": script["content_blocks"],
                "expected_revision": script["content_revision"],
            },
        ).status_code
        == 409
    )
    assert "新稿" in client.get(root + f"/scripts/{script['id']}/fountain").text
    with sessions() as db:
        db.get(Script, uuid.UUID(script["id"])).status = "locked"
        db.commit()
    assert (
        client.put(root + f"/scripts/{script['id']}/blocks", json={"blocks": changed}).status_code
        == 423
    )


def test_foreign_or_archived_story_never_reaches_adapter(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    with sessions() as db:
        novel = Novel(project_id=ids["other"], code="OTHER-N", title="私有小说")
        db.add(novel)
        db.flush()
        chapter = Chapter(novel_id=novel.id, ordinal=1, title="私有章节", content="不能跨项目使用")
        db.add(chapter)
        db.flush()
        foreign_script = Script(project_id=ids["other"], code="OTHER-S", title="私有剧本")
        db.add(foreign_script)
        db.commit()
        chapter_id, novel_id, script_id = str(chapter.id), str(novel.id), str(foreign_script.id)
    monkeypatch.setattr(
        service,
        "_strategy",
        lambda *_: (_ for _ in ()).throw(AssertionError("Unauthorized model call")),
    )
    script = post_ok(client, root + "/scripts", {"title": "当前剧本"})
    for path, data in [
        ("/scripts/from-chapter", {"chapter_id": chapter_id}),
        ("/extract-entities", {"chapter_id": chapter_id}),
        (f"/scripts/{script['id']}/apply-scenes", {"chapter_id": chapter_id, "scenes": []}),
        ("/settings", {"novel_id": novel_id, "category": "world", "name": "越界"}),
    ]:
        assert client.post(root + path, json=data).status_code == 404
    assert (
        client.post(root + "/novels/decompose", params={"chapter_id": chapter_id}).status_code
        == 404
    )
    assert client.get(root + f"/scripts/{script_id}/scenes").status_code == 404
    own = post_ok(client, root + "/settings", {"category": "character", "name": "写作者设定"})
    with sessions() as db:
        from sqlalchemy import select

        member = db.scalar(
            select(Membership).where(
                Membership.project_id == uuid.UUID(ids["project"]),
                Membership.user_id == ids["user"],
            )
        )
        member.role = "writer"
        db.commit()
    assert client.post(root + f"/settings/{own['id']}/to-asset").status_code == 403
