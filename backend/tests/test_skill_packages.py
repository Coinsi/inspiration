"""Skill packages, reproducible use and shared creative preferences."""

# ruff: noqa: F811
import io
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.deps import ProjectContext
from app.core.errors import CapabilityUnsupported, Forbidden
from app.models.canvas import CanvasRun
from app.models.generation import GenerationJob
from app.models.identity import Membership, Project, User
from app.modules.agent import tools
from app.modules.skill.packages import bundle, parse_package
from app.modules.skill.schemas import SkillIn
from tests.test_canvas import node, tick
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def archive(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, value in files.items():
            z.writestr(name, value)
    return out.getvalue()


def package():
    return parse_package(
        archive(
            {
                "story/SKILL.md": (
                    "---\nname: 镜头方法\ndescription: 保持连续性\n---\n"
                    "# 镜头方法\n参考 references/rules.md\nassets/sample.bin scripts/tool.py"
                ),
                "story/references/rules.md": "原始参考：服装保持蓝色",
                "story/scripts/tool.py": "NEVER_EXECUTE_THIS",
                "story/assets/sample.bin": b"\x00\xff",
            }
        ),
        "story.zip",
    )


def test_roundtrip_and_rejection_boundaries():
    doc = {**package(), "name":"导出自定义名称", "required_tools":["object.read"]}
    SkillIn(**doc)
    result = parse_package(bundle(doc), "export.zip")
    assert result["name"] == doc["name"]
    assert result["files"] == doc["files"]
    assert result["required_tools"] == doc["required_tools"]
    for files in (
        {"../SKILL.md": "bad"},
        {"SKILL.md": "okay", "../escape.txt": "bad"},
        {"SKILL.md": "okay", "a.md": "one", "A.md": "two"},
        {"one/SKILL.md": "one", "two/SKILL.md": "two"},
        {"SKILL.md": "---\nname: [not, text]\n---\ntext"},
        {"SKILL.md": "okay", "huge.md": "x" * 1_000_001},
    ):
        with pytest.raises(CapabilityUnsupported):
            parse_package(archive(files), "bad.zip")
    with pytest.raises(ValueError):
        SkillIn(**{**doc, "files": [{"path": "skill.md", "content": "collision"}]})


def test_package_catalog_copy_and_generation_provenance(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    preview = client.post(
        root + "/skills/import-preview",
        files={"file": ("story.zip", bundle(package()), "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    assert client.get(root + "/skills/catalog").json()["total"] == 0
    doc = preview.json()
    skill = post_ok(client, root + "/skills", {**doc, "category": "shot"})
    # Search indexes the entry, name and description; attachments remain in the reader.
    listing = client.get(root + "/skills/catalog?q=连续性&category=shot").json()
    assert listing["total"] == 1 and listing["items"][0]["file_count"] == 4
    assert "instructions" not in listing["items"][0] and "files" not in listing["items"][0]
    exported = client.get(root + f"/skills/{skill['id']}/bundle")
    assert parse_package(exported.content, "a.zip")["files"] == doc["files"]
    changed = {**doc, "instructions": "新的方法", "revision": 1}
    assert client.put(root + f"/skills/{skill['id']}", json=changed).status_code == 200
    used = [{"id": skill["id"], "revision": 1}]
    job = post_ok(
        client,
        root + f"/shots/{ids['shot']}/generate",
        {"skills": used, "prompt_override": "拍摄走廊", "count": 1},
    )
    with sessions() as db:
        snap = db.get(GenerationJob, uuid.UUID(job["id"])).input_snapshot
        assert "原始参考：服装保持蓝色" in snap["prompt"] and "新的方法" not in snap["prompt"]
        assert "NEVER_EXECUTE_THIS" not in snap["prompt"]
        assert snap["skills"][0]["revision"] == 1
        assert len(snap["skills"][0]["files"]) == 2
    other = f"/api/v1/projects/{ids['other']}"
    assert (
        client.post(
            root + "/skills/copy",
            json={
                "source_project_id": str(ids["other"]),
                "source_skill_id": skill["id"],
                "revision": 2,
            },
        ).status_code
        == 403
    )
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    copied = post_ok(
        client,
        other + "/skills/copy",
        {"source_project_id": str(ids["project"]), "source_skill_id": skill["id"], "revision": 2},
    )
    assert copied["source_metadata"]["origin_id"] == skill["id"]
    assert client.get(root + "/skills/catalog?scope=mine").json()["total"] == 2
    assert (
        client.put(other + f"/skills/{copied['id']}/favorite", json={"favorite": True}).status_code
        == 200
    )
    favorites = client.get(root + "/skills/catalog?scope=favorites").json()
    assert favorites["total"] == 1 and favorites["items"][0]["id"] == copied["id"]
    assert favorites["items"][0]["favorite"] is True
    assert (
        client.put(other + f"/skills/{copied['id']}/favorite", json={"favorite": False}).status_code
        == 200
    )
    assert client.get(root + "/skills/catalog?scope=favorites").json()["total"] == 0
    post_ok(client, root + f"/skills/{skill['id']}/archive", {"revision": 2, "archived": True})
    assert (
        client.post(root + f"/shots/{ids['shot']}/generate", json={"skills": used}).status_code
        == 409
    )


def test_frozen_canvas_skill_and_agent_file_read(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    skill = post_ok(client, root + "/skills", package())
    canvas = post_ok(client, root + "/canvases", {"name": "技能画布"})
    path = root + f"/canvases/{canvas['id']}"
    n = node("a", "generate", ids["shot"])
    n["data"]["generate"]["skills"] = [{"id": skill["id"], "revision": 1}]
    doc = client.put(
        path, json={"name": "技能画布", "revision": 0, "document": {"nodes": [n], "edges": []}}
    ).json()
    run = post_ok(
        client, path + "/runs", {"revision": doc["revision"], "request_key": str(uuid.uuid4())}
    )
    post_ok(client, root + f"/skills/{skill['id']}/archive", {"revision": 1, "archived": True})
    tick(sessions, run["id"], 4)
    state = client.get(path + "/runs").json()[0]
    assert state["status"] == "succeeded", state
    with sessions() as db:
        r = db.get(CanvasRun, uuid.UUID(run["id"]))
        snapshots = r.snapshot["skill_snapshots"]["a"]
        ctx = ProjectContext(
            db.get(Project, uuid.UUID(ids["project"])),
            db.scalar(select(Membership).where(Membership.project_id == ids["project"])),
            db.get(User, uuid.UUID(str(ids["user"]))),
        )
        fake = SimpleNamespace(skill_snapshot=snapshots)
        result = tools.execute(
            db, ctx, fake, "skill.read_file", {"id": skill["id"], "path": "references/rules.md"}
        )
        assert result["content"] == "原始参考：服装保持蓝色" and result["revision"] == 1
        chunk = tools.execute(
            db,
            ctx,
            fake,
            "skill.read_file",
            {"id": skill["id"], "path": "references/rules.md", "offset": 0, "limit": 4},
        )
        rest = tools.execute(
            db,
            ctx,
            fake,
            "skill.read_file",
            {"id": skill["id"], "path": "references/rules.md", "offset": chunk["next_offset"]},
        )
        assert chunk["content"] + rest["content"] == result["content"]
        with pytest.raises(Forbidden):
            tools.execute(
                db,
                ctx,
                SimpleNamespace(skill_snapshot=[]),
                "skill.read_file",
                {"id": skill["id"], "path": "references/rules.md"},
            )


def test_creative_preferences_conflict_permissions_and_real_prompt(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    path = root + "/creative-preferences"
    original = client.get(path).json()
    data = {
        **original,
        "image_count": 2,
        "generation_guidance": "使用胶片颗粒",
        "assistant_guidance": "保留人物口癖",
    }
    saved = client.put(path, json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert client.put(path, json=data).status_code == 409
    assert client.put(path, json={**saved.json(), "image_count": 0}).status_code == 422
    job = post_ok(
        client, root + f"/shots/{ids['shot']}/generate", {"prompt_override": "用户画面", "count": 1}
    )
    with sessions() as db:
        prompt = db.get(GenerationJob, uuid.UUID(job["id"])).input_snapshot["prompt"]
        assert "使用胶片颗粒" in prompt and "用户画面" in prompt
    captured = {}
    from app.adapters.assist.mock import MockAssist

    original_propose = MockAssist.propose

    def record(self, **kwargs):
        captured.update(kwargs)
        return original_propose(self, **kwargs)

    monkeypatch.setattr(MockAssist, "propose", record)
    skill = post_ok(client, root + "/skills", package())
    chat = post_ok(
        client, root + "/assist/chats", {"target_type": "shot", "target_id": ids["shot"]}
    )
    post_ok(
        client,
        root + f"/assist/chats/{chat['id']}/messages",
        {"content": "检查画面", "engine": "mock", "skills": [{"id": skill["id"], "revision": 1}]},
    )
    assert (
        "保留人物口癖" in captured["instruction"]
        and "原始参考：服装保持蓝色" in captured["instruction"]
    )
    messages = client.get(root + f"/assist/chats/{chat['id']}").json()["messages"]
    assert messages[0]["skills"][0]["revision"] == 1
    with sessions() as db:
        member = db.scalar(select(Membership).where(Membership.project_id == ids["project"]))
        member.role = "viewer"
        db.commit()
    assert client.put(path, json=saved.json()).status_code == 403
    assert client.get(path).status_code == 200


def test_github_import_and_sync_preview_never_overwrites(studio, monkeypatch):
    import json

    from app.modules.skill import packages

    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    commit = ["a" * 40]
    calls = []

    class Response(io.BytesIO):
        def __init__(self, url, body):
            super().__init__(body)
            self.url = url

    def fetch(req, timeout):
        url = req.full_url
        calls.append(url)
        if "/commits/" in url:
            body = json.dumps({"sha": commit[0]}).encode()
        elif "codeload.github.com" in url:
            body = archive({"repo/skills/story/SKILL.md": "# 来源方法\n内容版本 " + commit[0]})
        else:
            body = b'{"default_branch":"main"}'
        return Response(url, body)

    monkeypatch.setattr(packages, "urlopen", fetch)
    preview = post_ok(
        client,
        root + "/skills/github-preview",
        {"url": "https://github.com/example/repo", "directory": "skills/story"},
    )
    assert preview["source_metadata"]["commit"] == "a" * 40
    skill = post_ok(client, root + "/skills", preview)
    commit[0] = "b" * 40
    update = post_ok(client, root + f"/skills/{skill['id']}/sync-preview", {"revision": 1})
    assert update["source_metadata"]["commit"] == "b" * 40
    current = client.get(root + f"/skills/{skill['id']}").json()
    assert current["source_metadata"]["commit"] == "a" * 40 and current["revision"] == 1
    body = {k: v for k, v in update.items() if k not in ("id", "archived")}
    saved = client.put(root + f"/skills/{skill['id']}", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    for bad in (
        "http://github.com/example/repo",
        "https://127.0.0.1/repo",
        "https://github.com.evil.test/repo",
    ):
        before = len(calls)
        assert client.post(root + "/skills/github-preview", json={"url": bad}).status_code == 422
        assert len(calls) == before


def test_catalog_pagination_and_agent_explicit_revision(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    doc = package()
    for index in range(26):
        post_ok(client, root + "/skills", {**doc, "name": f"method-{index:02d}"})
    page1 = client.get(root + "/skills/catalog?sort=name").json()
    page2 = client.get(root + "/skills/catalog?sort=name&offset=24").json()
    assert page1["total"] == 26 and len(page1["items"]) == 24 and len(page2["items"]) == 2
    assert not ({s["id"] for s in page1["items"]} & {s["id"] for s in page2["items"]})
    skill = page2["items"][0]
    assert (
        client.put(
            root + f"/skills/{skill['id']}", json={**doc, "revision": 1, "instructions": "changed"}
        ).status_code
        == 200
    )
    run = post_ok(
        client,
        root + "/agent/runs",
        {
            "goal": "读取选定技能",
            "engine": "mock",
            "request_key": str(uuid.uuid4()),
            "skills": [skill["id"]],
            "skill_versions": [{"id": skill["id"], "revision": 1}],
        },
    )
    assert run["skills"][0]["revision"] == 1
    with sessions() as db:
        from app.models.agent import AgentRun

        snapshot = db.get(AgentRun, uuid.UUID(run["id"])).skill_snapshot[0]
        assert snapshot["instructions"] == doc["instructions"]
