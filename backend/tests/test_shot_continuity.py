"""Exercise the authoring and generation endpoints against isolated PostgreSQL schemas."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.adapters.contracts import GenerationResult
from app.adapters.generation.mock import MockProvider
from app.models.generation import Generation, GenerationJob
from app.models.timeline import Timeline, TimelineItem
from app.modules.generation.shot_batches import run_once
from tests.test_library_migrations import library_migration_schema  # noqa: F401
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_asset_reuse_shot_visibility_and_output_lineage(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    setting = post_ok(
        client, root + "/settings", {"name": "秦禹", "category": "character", "content": "青年"}
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: post_ok(client, root + f"/settings/{setting['id']}/to-asset"), range(2)
            )
        )
    asset = results[0]
    assert results[1]["id"] == asset["id"]
    path = root + f"/assets/{asset['id']}"
    meta = {
        "aliases": ["小秦"],
        "visual_traits": {"face": "黑眼睛", "wardrobe": "旧外衣"},
        "looks": {"青年": {"wardrobe": "黑风衣"}},
    }
    edited = client.patch(path, json={"metadata": meta}).json()
    script = post_ok(client, root + "/scripts", {"title": "街口"})
    assert (
        post_ok(
            client,
            root + "/apply-entities",
            {"script_id": script["id"], "entities": [{"type": "character", "name": "小秦"}]},
        )[0]["id"]
        == asset["id"]
    )

    def brief(name, aspect, look=""):
        return {
            "title": name,
            "description": "秦禹走到街口",
            "still_prompt": name + "，近景，侧面冷光",
            "motion_prompt": "镜头跟随青年向右移动",
            "subjects": [
                {
                    "name": "小秦",
                    "type": "character",
                    "aspects": [aspect],
                    "look": look,
                    "evidence": "秦禹走到街口",
                }
            ],
        }

    scenes = {
        "scenes": [
            {
                "title": "路口",
                "body": "一场戏",
                "shots": [
                    brief("正脸", "face"),
                    brief("过肩", "wardrobe", "青年"),
                    brief("旧版", "face"),
                ],
            }
        ]
    }
    scene_path = root + f"/scripts/{script['id']}/apply-scenes"
    scene = post_ok(client, scene_path, scenes)[0]
    assert post_ok(client, scene_path, scenes)[0]["id"] == scene["id"]
    shots = client.get(root + f"/scenes/{scene['id']}/shots").json()
    face, sleeve, frozen = [root + f"/shots/{s['id']}" for s in shots]
    ref = client.get(frozen + "/asset-refs").json()[0]
    assert client.put(
        frozen + "/asset-refs",
        json={
            "refs": [
                {**ref, "ref_mode": "pinned", "pinned_version_id": edited["current_version_id"]}
            ]
        },
    ).is_success
    assert "黑风衣" in client.get(sleeve + "/compose-prompt").json()["final_prompt"]
    assert "跟随" not in client.get(face + "/compose-prompt").json()["final_prompt"]
    assert "跟随" in client.get(face + "/compose-prompt?media_type=video").json()["final_prompt"]
    for s in [face, sleeve, frozen]:
        assert post_ok(client, s + "/generate", {})["status"] == "succeeded"
    source = client.get(
        root + "/generations", params={"target_type": "shot", "target_id": shots[0]["id"]}
    ).json()[0]
    # Derivative in another shot and a real cut referencing only that derivative.
    with sessions() as db:
        image = db.get(Generation, uuid.UUID(source["id"]))
        video = Generation(
            project_id=image.project_id,
            job_id=image.job_id,
            target_type="shot",
            target_id=uuid.UUID(shots[1]["id"]),
            provider="mock",
            output_type="video",
            input_refs={"first_frame_id": source["id"]},
        )
        timeline = Timeline(project_id=image.project_id, name="剪辑一")
        legacy = Generation(
            project_id=image.project_id,
            job_id=image.job_id,
            target_type="shot",
            target_id=image.target_id,
            provider="mock",
            output_type="image",
            input_refs={},
        )
        db.add_all([video, timeline, legacy])
        db.flush()
        db.add(TimelineItem(timeline_id=timeline.id, generation_id=video.id, ordinal=1))
        db.commit()
    change = {"metadata": {**meta, "visual_traits": {**meta["visual_traits"], "face": "灰眼睛"}}}
    impact = post_ok(client, path + "/impact-preview", change)
    assert impact["untracked_outputs"] == 1
    assert {s["title"]: s["state"] for s in impact["shots"]} == {
        "正脸": "affected",
        "旧版": "pinned",
    }
    assert {o["type"] for o in impact["outputs"]} == {"image", "video"}
    assert [c["timeline"] for c in impact["clips"]] == ["剪辑一"]
    assert client.patch(path, json=change).is_success
    assert "灰眼睛" in client.get(face + "/compose-prompt").json()["final_prompt"]
    assert "黑眼睛" in client.get(frozen + "/compose-prompt").json()["final_prompt"]
    assert "灰眼睛" not in client.get(sleeve + "/compose-prompt").json()["final_prompt"]


def test_batch_retains_success_retries_one_and_never_resends_unknown(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    original_submit, original_poll = MockProvider.submit, MockProvider.poll
    attempts = {}

    def submit(self, req):
        prompt = req.prompt
        attempts[prompt] = attempts.get(prompt, 0) + 1
        if prompt == "uncertain":
            raise TimeoutError("connection lost")
        handle = original_submit(self, req)
        handle.raw["prompt"] = prompt
        return handle

    def poll(self, handle):
        label = handle.raw["prompt"]
        if label == "busy" and attempts[label] == 1:
            return GenerationResult.confirmed_failure("capacity", kind="provider_capacity")
        if label == "moderated":
            return GenerationResult.confirmed_failure("review input", kind="input_moderation")
        return original_poll(self, handle)

    monkeypatch.setattr(MockProvider, "submit", submit)
    monkeypatch.setattr(MockProvider, "poll", poll)
    scene = client.get(root + f"/shots/{ids['shot']}").json()["scene_id"]
    scope = root + f"/scenes/{scene}"
    keys = []
    for name in ("busy", "uncertain", "moderated", "good"):
        shot = post_ok(client, scope + "/shots", {"title": name, "description": name})
        assert client.patch(
            root + f"/shots/{shot['id']}", json={"prompt_override": name}
        ).is_success
        keys.append(shot["id"])
    payload = {"shot_ids": keys, "request_key": str(uuid.uuid4()), "provider": "mock"}

    def start(data):
        r = client.post(scope + "/generation-batches", json=data)
        assert r.status_code == 202, r.text
        return r.json()

    batch = start(payload)
    assert [i["action"] for i in batch["items"]] == ["retry", "verify", "edit", None]
    assert attempts == {n: 1 for n in ("busy", "uncertain", "moderated", "good")}
    assert start(payload)["id"] == batch["id"]
    other = start({**payload, "request_key": str(uuid.uuid4())})
    assert [i["job_id"] for i in other["items"]] == [i["job_id"] for i in batch["items"]]
    for item in batch["items"][1:]:
        assert (
            client.post(
                root + f"/shot-batch-items/{item['id']}/retry", json={"job_id": item["job_id"]}
            ).status_code
            == 409
        )
    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(batch["items"][0]["job_id"]))
        job.updated_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    assert run_once(sessions)
    while run_once(sessions):
        pass
    runs = client.get(scope + "/generation-batches").json()
    for result in runs:
        assert result["items"][0]["status"] == "succeeded"
        assert result["items"][-1]["job_id"] == batch["items"][-1]["job_id"]
    assert attempts == {"busy": 2, "uncertain": 1, "moderated": 1, "good": 1}


def test_partial_scene_retry_preserves_other_shots_and_order(studio, monkeypatch):
    from app.adapters.contracts import StoryboardShotSuggestion
    from app.adapters.decomposition.mock import MockDecomposer
    from app.core.errors import ProviderError
    from app.modules.narrative import tool_tasks
    from tests.test_creative_tool_tasks import setup_tools

    client, _, _, root, script = setup_tools(studio, monkeypatch)
    blocks = [
        block
        for label in ("街口", "街边", "路灯")
        for block in [
            {"block_type": "scene_heading", "text": label},
            {"block_type": "action", "text": label + "下雪"},
        ]
    ]
    assert client.put(root + f"/scripts/{script['id']}/blocks", json={"blocks": blocks}).is_success
    calls = []

    def shots(self, text):
        calls.append(text)
        if text == "街边下雪" and calls.count(text) == 1:
            raise ProviderError("当前场次请求失败")
        return [StoryboardShotSuggestion(title=text, still_prompt=text + "，远景")]

    monkeypatch.setattr(MockDecomposer, "suggest_storyboard_shots", shots)
    jobs = root + "/creative-tool-tasks"
    data = {
        "operation": "script_decompose",
        "target_id": script["id"],
        "request_key": str(uuid.uuid4()),
    }
    task = client.post(jobs, json=data).json()

    def scenes():
        return client.get(root + f"/scripts/{script['id']}/scenes").json()

    def receipt():
        return client.get(
            jobs, params={"operation": data["operation"], "target_id": script["id"]}
        ).json()

    assert tool_tasks.run_once()
    assert [bool(s.get("error")) for s in receipt()["result"]["output"]["scenes"]] == [
        False,
        True,
        False,
    ]
    post_ok(client, jobs + "/" + task["id"] + "/apply", {})
    original = {s["title"]: s["id"] for s in scenes()}
    assert list(original) == ["街口", "路灯"]
    assert client.post(jobs + "/" + task["id"] + "/retry-scenes").status_code == 202
    assert tool_tasks.run_once()
    post_ok(client, jobs + "/" + task["id"] + "/apply", {})
    assert [s["title"] for s in scenes()] == ["街口", "街边", "路灯"]
    assert all(s["id"] == original[s["title"]] for s in scenes() if s["title"] in original)
    assert calls == ["街口下雪", "街边下雪", "路灯下雪", "街边下雪"]
    assert (
        client.post(jobs, json={**data, "request_key": str(uuid.uuid4())}).json()["id"]
        == task["id"]
    )


def test_settings_chapter_recovery_and_time_scoped_evidence(studio, monkeypatch):
    from types import SimpleNamespace

    from app.adapters.contracts import SettingDraft
    from app.adapters.decomposition.mock import MockDecomposer
    from app.core import database
    from app.core.errors import ProviderError
    from app.modules.setting import service

    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    chapter_text = ["清晨披风", "正午摘帽", "夜晚围巾"]
    text = "\n".join(f"第{num}章 路口\n{line}。" for num, line in zip("一二三", chapter_text))
    novel = client.post(
        root + "/novels/import", files={"file": ("story.txt", text.encode(), "text/plain")}
    ).json()
    path = root + f"/novels/{novel['id']}/extract-settings"
    queued = []

    class CaptureThread:
        def __init__(self, target, args, daemon):
            self.run = lambda: target(*args)

        def start(self):
            queued.append(self.run)

    monkeypatch.setattr(service, "threading", SimpleNamespace(Thread=CaptureThread))
    monkeypatch.setattr(database, "SessionLocal", sessions)
    strategy = MockDecomposer()
    calls = []

    def extract(text, existing):
        fact = next(line for line in chapter_text if line in text)
        calls.append(fact)
        if fact == chapter_text[1] and calls.count(fact) == 1:
            raise ProviderError("通道暂不可用")
        return [
            SettingDraft(
                category="character", name="秦禹", content=fact, chapter_fact=fact, quote=fact
            )
        ]

    monkeypatch.setattr(strategy, "extract_settings", extract)
    monkeypatch.setattr(service, "_strategy", lambda *args: strategy)
    job = post_ok(client, path, {"concurrency": 1})
    queued.pop()()
    partial = client.get(path + "/latest").json()
    assert partial["status"] == "partial" and partial["chapter_progress"]["done"] == [1, 3]
    assert list(partial["chapter_progress"]["failed"]) == ["2"]
    assert post_ok(client, path, {"concurrency": 1})["id"] == job["id"] and not queued
    post_ok(client, root + f"/setting-extractions/{job['id']}/retry")
    queued.pop()()
    assert calls == [*chapter_text, chapter_text[1]]
    assert client.get(path + "/latest").json()["status"] == "succeeded"
    overlap = post_ok(client, path, {"from_chapter": 2, "to_chapter": 3})
    assert overlap["status"] == "succeeded" and overlap["done_chapters"] == 2 and not queued
    result = client.get(root + "/settings", params={"novel_id": novel["id"]}).json()[0]
    assert (
        result["source_notes"]["2"]["quote"] == chapter_text[1]
        and chapter_text[2] in result["content"]
    )
    with sessions() as db:
        early = service.settings_context(
            db, uuid.UUID(ids["project"]), uuid.UUID(novel["id"]), 1, subject_text="秦禹"
        )
        assert chapter_text[0] in early and chapter_text[2] not in early


def test_existing_adaptation_and_concurrent_clicks_make_one_model_call(studio, monkeypatch):
    from app.adapters.decomposition.mock import MockDecomposer

    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    novel = client.post(
        root + "/novels/import",
        files={
            "file": (
                "story.txt",
                "第一章 清晨\\n少年推开门。".replace("\\n", "\n").encode(),
                "text/plain",
            )
        },
    ).json()
    chapter = client.get(root + f"/novels/{novel['id']}/directory").json()["chapters"][0]["id"]
    adapt = MockDecomposer.adapt_chapter_to_script
    calls = []

    def counted(self, text, context):
        calls.append(text)
        return adapt(self, text, context)

    monkeypatch.setattr(MockDecomposer, "adapt_chapter_to_script", counted)
    data = {"chapter_id": chapter, "strategy": "mock"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: post_ok(client, root + "/scripts/from-chapter", data), range(2))
        )
    assert results[0]["id"] == results[1]["id"] and len(calls) == 1


def test_continuity_upgrade_and_rollback(library_migration_schema):
    from sqlalchemy import inspect

    from alembic import command

    connection, config, schema = library_migration_schema
    command.upgrade(config, "0069_credit_grants")
    original = set(inspect(connection).get_table_names(schema=schema))
    command.upgrade(config, "0070_shot_continuity")
    assert set(inspect(connection).get_table_names(schema=schema)) - original == {
        "asset_story_source",
        "shot_batch",
        "shot_batch_item",
    }
    command.downgrade(config, "0069_credit_grants")
    assert set(inspect(connection).get_table_names(schema=schema)) == original
    command.upgrade(config, "0070_shot_continuity")
    assert "source_notes" in {
        c["name"] for c in inspect(connection).get_columns("setting", schema=schema)
    }


def test_adopted_still_is_used_by_batch_video_and_explicit_opt_out(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    path = root + f"/shots/{ids['shot']}"
    scene = client.get(path).json()["scene_id"]
    image_job = post_ok(client, path + "/generate", {})
    image = client.get(
        root + "/generations", params={"target_type": "shot", "target_id": ids["shot"]}
    ).json()[0]
    post_ok(client, root + f"/generations/{image['id']}/select", {})
    caps = (
        MockProvider()
        .capabilities()
        .model_copy(update={"features": {"first_frame"}, "max_reference_images": 1})
    )
    monkeypatch.setattr(MockProvider, "capabilities", lambda self: caps)
    response = client.post(
        root + f"/scenes/{scene}/generation-batches",
        json={
            "shot_ids": [ids["shot"]],
            "request_key": str(uuid.uuid4()),
            "provider": "mock",
            "media_type": "video",
        },
    )
    assert response.status_code == 202, response.text
    batch = response.json()
    assert batch["items"][0]["status"] == "succeeded"
    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(batch["items"][0]["job_id"]))
        assert job.input_snapshot["first_frame_id"] == image["id"]
        assert job.input_snapshot["references"][0]["role"] == "first_frame"
        video = db.scalar(select(Generation).where(Generation.job_id == job.id))
        assert video.input_refs["first_frame_id"] == image["id"]
    again = post_ok(client, path + "/generate", {"request_type": "video"})
    assert again["id"] == batch["items"][0]["job_id"]
    plain = post_ok(client, path + "/generate", {"request_type": "video", "use_references": False})
    assert plain["status"] == "succeeded" and plain["id"] not in {again["id"], image_job["id"]}
    assert plain["input_snapshot"]["first_frame_id"] is None


def test_duplicate_generation_survives_random_route_order(studio, monkeypatch):
    from app.modules.generation import model_catalog
    from tests.test_creative_models import group, pair

    client, root, profiles = pair(studio)
    model = group(
        client,
        root,
        profiles,
        routes=[{"profile_id": p["id"], "priority": 100, "weight": 100} for p in profiles],
    )
    orders = []

    def ordered(routes):
        result = list(routes)
        if len(orders) % 2:
            result.reverse()
        orders.append([row["profile_id"] for row in result])
        return result

    monkeypatch.setattr(model_catalog, "ordered", ordered)
    path = root + f"/shots/{studio[2]['shot']}/generate"
    first = post_ok(client, path, {"provider": model["provider_name"]})
    again = post_ok(client, path, {"provider": model["provider_name"]})
    assert orders[0] == orders[-1][::-1] and len(orders[0]) == 2
    assert first["id"] == again["id"] and first["status"] == "succeeded"


def test_ambiguous_people_need_confirmation_and_evidence_for_shot_links(studio):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    one, two = [
        post_ok(client, root + "/scripts", {"title": title})["id"] for title in ("剧本甲", "剧本乙")
    ]

    def entities(script, name, evidence="", **decision):
        return client.post(
            root + "/apply-entities",
            json={
                "script_id": script,
                "entities": [{"type": "character", "name": name, "evidence": evidence, **decision}],
            },
        )

    old = entities(one, "秦禹").json()[0]
    assert entities(two, "秦禹").status_code == 409
    assert entities(two, "秦禹", asset_id=old["id"]).json()[0]["id"] == old["id"]
    keeper = entities(two, "女人", "看店的女人叠起了账本").json()[0]
    assert entities(two, "女人", "巷里的女人抬手招车").status_code == 409
    second = entities(two, "女人", "巷里的女人抬手招车", separate=True).json()[0]
    assert second["id"] != keeper["id"]
    assert entities(two, "女人", "巷里的女人抬手招车").json()[0]["id"] == second["id"]
    who = {"name": "秦禹", "type": "character", "evidence": "他在墙边站住"}
    scene = post_ok(
        client,
        root + f"/scripts/{two}/apply-scenes",
        {
            "scenes": [
                {
                    "title": "墙边",
                    "body": "他在墙边站住",
                    "shots": [{"title": "背影", "subjects": [who]}],
                }
            ]
        },
    )[0]
    shot = client.get(root + f"/scenes/{scene['id']}/shots").json()[0]
    assert client.get(root + f"/shots/{shot['id']}/asset-refs").json() == []
    assert shot["storyboard"]["unresolved_subjects"][0]["name"] == "秦禹"
    nickname = {"type": "character", "name": "小秦", "evidence": "小秦推开门"}
    proposal = {
        "title": "门口",
        "body": "小秦推开门",
        "shots": [{"title": "推门", "subjects": [nickname]}],
    }
    scene = post_ok(client, root + f"/scripts/{two}/apply-scenes", {"scenes": [proposal]})[0]
    waiting = client.get(root + f"/scenes/{scene['id']}/shots").json()[0]
    ref_path = root + f"/shots/{waiting['id']}/asset-refs"
    assert client.get(ref_path).json() == []
    assert entities(two, "小秦", "秦禹说，叫我小秦。").status_code == 409
    assert entities(two, "小秦", asset_id=old["id"]).json()[0]["id"] == old["id"]
    assert [r["asset_id"] for r in client.get(ref_path).json()] == [old["id"]]
    assert client.put(ref_path, json={"refs": []}).is_success
    assert entities(two, "小秦").json()[0]["id"] == old["id"] and client.get(ref_path).json() == []
    another = post_ok(
        client,
        root + f"/scripts/{two}/apply-scenes",
        {"scenes": [{**proposal, "title": "另一扇门"}]},
    )[0]
    next_shot = client.get(root + f"/scenes/{another['id']}/shots").json()[0]
    assert (
        client.get(root + f"/shots/{next_shot['id']}/asset-refs").json()[0]["asset_id"] == old["id"]
    )


def test_removed_shot_does_not_abort_other_items(studio):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    scene = client.get(root + f"/shots/{ids['shot']}").json()["scene_id"]
    path = root + f"/scenes/{scene}"
    removed = post_ok(client, path + "/shots", {"title": "已删除的镜头"})
    assert client.delete(root + f"/shots/{removed['id']}").is_success
    response = client.post(
        path + "/generation-batches",
        json={
            "request_key": str(uuid.uuid4()),
            "provider": "mock",
            "shot_ids": [ids["shot"], removed["id"]],
        },
    )
    assert response.status_code == 202, response.text
    items = {i["shot_id"]: i for i in response.json()["items"]}
    assert items[ids["shot"]]["status"] == "succeeded"
    assert items[removed["id"]]["status"] == "failed" and items[removed["id"]]["job_id"] is None


def test_candidate_created_in_same_request_survives_confirmation(studio):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    script = post_ok(client, root + "/scripts", {"title": "旧街"})
    data = {
        "script_id": script["id"],
        "entities": [
            {"type": "character", "name": "秦禹"},
            {"type": "character", "name": "小秦", "evidence": "秦禹说，叫我小秦。"},
            {"type": "location", "name": "旧街"},
        ],
    }
    first = client.post(root + "/apply-entities", json=data)
    assert first.status_code == 409
    detail = first.json()["error"]["detail"]
    assert detail["saved_assets"] == 2 and detail["index"] == 1
    candidate = detail["candidates"][0]["id"]
    assert client.get(root + "/assets/" + candidate).json()["name"] == "秦禹"
    data["entities"][1]["asset_id"] = candidate
    assert len(post_ok(client, root + "/apply-entities", data)) == 2
    assert len(client.get(root + "/assets", params={"q": "旧街"}).json()) == 1
