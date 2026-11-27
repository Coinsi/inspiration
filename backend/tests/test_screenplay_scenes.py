"""Exercise the editor, scene roster, production and review across an actual script revision."""
# ruff: noqa: F811

import uuid

from sqlalchemy import func, select, text

from alembic import command
from app.adapters.contracts import EntityDraft, ShotSubject, StoryboardShotSuggestion
from app.adapters.decomposition.mock import MockDecomposer
from app.models.generation import GenerationJob
from app.models.narrative import Scene, Script
from app.models.shot import ShotAssetRef
from app.modules.narrative import service
from tests.test_library_migrations import (  # noqa: F401
    library_migration_schema,
    seed_legacy_library,
)
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def block(key, kind, text):
    return {"id": key, "block_type": kind, "text": text}


def test_speaker_and_dialogue_share_the_line_as_evidence(studio):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    script = post_ok(client, root + "/scripts", {"title": "换班"})
    path = f"{root}/scripts/{script['id']}"
    blocks = [
        block("h", "scene_heading", "INT. 值班室 - 夜"),
        block("a", "action", "阿岚放下账本。"),
        block("c", "character", "阿岚"),
        block("d", "dialogue", "该换班了。"),
        block("c2", "character", "小齐"),
        block("d2", "dialogue", "该换班了。"),
    ]
    assert client.put(path + "/blocks", json={"blocks": blocks}).is_success
    scenes = post_ok(client, path + "/decompose?strategy=mock")["scenes"]
    post_ok(client, path + "/apply-scenes", {"scenes": scenes})
    shots = client.get(f"{root}/scenes/{scenes[0]['scene_id']}/shots").json()
    assert len(shots) == 3
    blocks[1]["text"] = "阿岚拿起账本。"
    assert client.put(path + "/blocks", json={"blocks": blocks}).is_success
    assert [r["shot_id"] for r in client.get(path + "/affected-shots").json()] == [shots[0]["id"]]
    blocks[2]["text"] = "阿月"
    assert client.put(path + "/blocks", json={"blocks": blocks}).is_success
    review = {r["shot_id"]: r["changes"] for r in client.get(path + "/affected-shots").json()}
    assert set(review) == {shots[0]["id"], shots[1]["id"]}
    assert review[shots[1]["id"]][0]["after"] == "阿月：该换班了。"


def test_migrates_in_place_and_restores_blocks_on_downgrade(library_migration_schema):
    import json

    conn, config, schema = library_migration_schema
    ids = seed_legacy_library(conn, schema)
    command.upgrade(config, "0070_shot_continuity")
    scene_id = conn.scalar(text("SELECT scene_id FROM shot WHERE id=:shot"), {"shot": ids["shot"]})
    script_id = conn.scalar(text("SELECT script_id FROM scene WHERE id=:id"), {"id": scene_id})
    blocks = [
        block("head", "scene_heading", "INT. 住处 - 夜"),
        block("p", "character", "秦禹"),
        block("act", "action", "推开木门。"),
        block("head2", "scene_heading", "INT. 住处 - 夜"),
    ]
    conn.execute(
        text("UPDATE scene SET title=:title, body=:body WHERE id=:id"),
        {"id": scene_id, "title": blocks[0]["text"], "body": "秦禹:\n推开木门。"},
    )
    conn.execute(
        text("UPDATE script SET content_blocks=CAST(:doc AS jsonb) WHERE id=:id"),
        {"id": script_id, "doc": json.dumps(blocks)},
    )
    command.upgrade(config, "0071_screenplay_scenes")
    rows = conn.execute(
        text("SELECT id, script_blocks FROM scene WHERE script_id=:id ORDER BY script_position"),
        {"id": script_id},
    ).all()
    assert len(rows) == 2 and rows[0].id == scene_id
    assert [b for row in rows for b in row.script_blocks] == blocks
    assert set(
        conn.scalars(text("SELECT name FROM scene_element WHERE scene_id=:id"), {"id": scene_id})
    ) == {"秦禹", "住处"}
    assert (
        conn.scalar(text("SELECT scene_id FROM shot WHERE id=:shot"), {"shot": ids["shot"]})
        == scene_id
    )
    command.downgrade(config, "0070_shot_continuity")
    assert (
        conn.scalar(text("SELECT content_blocks FROM script WHERE id=:id"), {"id": script_id})
        == blocks
    )
    assert (
        conn.scalar(text("SELECT body FROM scene WHERE id=:id"), {"id": scene_id})
        == "秦禹:\n推开木门。"
    )


def test_same_location_changes_only_the_shots_using_the_action(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    actor = post_ok(client, root + "/assets", {"name": "秦禹", "type": "character"})
    prop = post_ok(client, root + "/assets", {"name": "米袋", "type": "prop"})
    script_id = post_ok(client, root + "/scripts", {"title": "改稿"})["id"]
    path = root + "/scripts/" + script_id
    blocks = [
        block("h1", "scene_heading", "INT. 住处 - 夜"),
        block("n", "character", "秦禹"),
        block("a", "action", "秦禹放下米袋。"),
        block("b", "action", "窗上结了霜。"),
        block("h2", "scene_heading", "INT. 住处 - 夜"),
        block("c", "action", "桌上放着米袋。"),
    ]
    saved = client.put(path + "/blocks", json={"blocks": blocks}).json()
    scenes = client.get(path + "/scenes").json()
    assert len(scenes) == 2 and scenes[0]["id"] != scenes[1]["id"]
    first, second = (s["id"] for s in scenes)
    before_shots = client.get(root + "/assets", params={"scene_id": first}).json()
    assert [a["id"] for a in before_shots] == [actor["id"]]
    appearances = client.get(root + f"/assets/{actor['id']}/appearances").json()
    assert [(s["scene_id"], s["shots"]) for s in appearances] == [(first, [])]
    assert appearances[0]["members"][0]["name"] == "秦禹"
    with sessions() as db:
        assert not db.get(Script, uuid.UUID(script_id)).legacy_blocks
        assert db.get(Scene, uuid.UUID(first)).body.startswith("秦禹:")

    class Strategy(MockDecomposer):
        def extract_entities(self, text):
            quote = "秦禹放下米袋。" if "放下" in text else "桌上放着米袋。"
            return [EntityDraft(name="米袋", type="prop", summary="粗麻袋口扎紧。", evidence=quote)]

        def suggest_storyboard_shots(self, text):
            quotes = ["秦禹放下米袋。", "窗上结了霜。"] if "结了霜" in text else ["桌上放着米袋。"]
            return [
                StoryboardShotSuggestion(
                    title=q,
                    description=q,
                    source_quotes=[q],
                    continuity_quotes=["秦禹放下米袋。"] if q.startswith("桌") else [],
                    subjects=[ShotSubject(type="prop", name="米袋", evidence=q)]
                    if "米袋" in q
                    else [],
                    still_prompt="手调前的定格画面",
                    motion_prompt="缓慢拉远",
                )
                for q in quotes
            ]

    monkeypatch.setattr(service, "_strategy", lambda *_: Strategy())
    extracted = post_ok(client, root + "/extract-entities", {"script_id": script_id})
    scoped_bag = next(item for item in extracted if item["name"] == "米袋")
    assert scoped_bag["scene_id"] == first and scoped_bag["element_id"]
    picked = {"entities": [scoped_bag], "script_id": script_id}
    assert [a["id"] for a in post_ok(client, root + "/apply-entities", picked)] == [prop["id"]]
    assert [a["id"] for a in post_ok(client, root + "/apply-entities", picked)] == [prop["id"]]
    proposal = post_ok(client, path + "/decompose")
    post_ok(client, path + "/apply-scenes", {"scenes": proposal["scenes"]})

    def by_scene(sid):
        return client.get(root + f"/scenes/{sid}/shots").json()

    carrying, window = by_scene(first)
    later = by_scene(second)[0]
    roster = client.get(root + f"/scenes/{first}/elements").json()
    bag = next(e for e in roster if e["asset_id"] == prop["id"])
    label = "陈旧青蓝色粗麻，不带花纹"
    stale = client.patch(root + f"/scenes/{first}/elements/{bag['id']}",
                         json={"expected_asset_id": actor["id"], "direction": "过期编辑"})
    assert stale.status_code == 409
    changed = client.patch(
        root + f"/scenes/{first}/elements/{bag['id']}", json={"expected_asset_id": prop["id"], "direction": label}
    )
    assert changed.is_success, changed.text
    appearances = client.get(root + f"/assets/{prop['id']}/appearances").json()
    assert [s["scene_id"] for s in appearances] == [first, second]
    assert [s["members"][0]["direction_excerpt"] for s in appearances] == [label, ""]
    assert [s["shots"][0]["id"] for s in appearances] == [carrying["id"], later["id"]]
    shot_path = root + f"/shots/{carrying['id']}"
    ref_path = shot_path + "/asset-refs"
    ref = client.get(ref_path).json()
    assert [item["asset_id"] for item in ref] == [prop["id"]]
    # Round-tripping an existing reference and matching an alias must preserve its scene and chosen look.
    linked = {"asset_id": prop["id"], "role": "prop", "usage": {**ref[0]["usage"], "look": "夜戏造型"}}
    assert client.put(ref_path, json={"refs": [linked]}).is_success
    subject = {"name": "粗麻口袋", "type": "prop", "look": "提议造型", "evidence": "秦禹放下米袋。"}
    assert client.patch(shot_path, json={"storyboard": {"unresolved_subjects": [subject]}}).is_success
    confirmed = client.put(ref_path, json={"refs": [{**linked, "resolves_subject": subject["name"]}]})
    assert confirmed.is_success, confirmed.text
    assert confirmed.json()[0]["id"] == ref[0]["id"]
    assert confirmed.json()[0]["usage"]["look"] == "夜戏造型"
    assert not client.get(shot_path).json()["storyboard"]["unresolved_subjects"]
    with sessions() as db:
        assert db.get(ShotAssetRef, uuid.UUID(ref[0]["id"])).usage["scene_element_id"] == bag["id"]
    assert not client.get(root + f"/shots/{window['id']}/asset-refs").json()
    post_ok(client, root + f"/shots/{carrying['id']}/generate", {"provider": "mock"})
    media_path = root + f"/generations?target_type=shot&target_id={carrying['id']}"
    generation = client.get(media_path).json()[0]
    assert label in generation["prompt_snapshot"] and not generation["earlier_script"]
    assert client.post(root + f"/generations/{generation['id']}/select").is_success
    assert client.patch(
        root + f"/shots/{carrying['id']}", json={"prompt_override": "手调的构图"}
    ).is_success
    blocks[2] = block("a", "action", "秦禹提着米袋出门。")
    revised = client.put(
        path + "/blocks", json={"blocks": blocks, "expected_revision": saved["content_revision"]}
    )
    assert revised.is_success, revised.text
    assert [s["id"] for s in client.get(path + "/scenes").json()] == [first, second]
    assert (
        client.post(path + "/apply-scenes", json={"scenes": proposal["scenes"]}).status_code == 409
    )
    review = {s["shot_id"]: s for s in client.get(path + "/affected-shots").json()}
    assert set(review) == {carrying["id"], later["id"]}
    assert review[later["id"]]["changes"][0]["after"] == blocks[2]["text"]
    assert client.get(media_path).json()[0]["earlier_script"]
    assert client.get(root + f"/shots/{carrying['id']}").json()["prompt_override"] == "手调的构图"
    assert by_scene(first)[0]["selected_generation_id"] == generation["id"]
    decision = path + f"/affected-shots/{carrying['id']}/confirm"
    assert client.post(decision, json={"revision": "0" * 64}).status_code == 409
    review_path = decision.removesuffix("/confirm")
    payload = {"revision": review[carrying["id"]]["revision"]}
    preview = post_ok(client, review_path + "/preview?strategy=mock", payload)
    assert preview["still_prompt"] == blocks[2]["text"]
    assert client.patch(root + f"/shots/{carrying['id']}", json={"title": "我的标题"}).is_success
    assert client.post(review_path + "/adopt", json=preview).status_code == 409
    preview = post_ok(client, review_path + "/preview?strategy=mock", payload)
    preview["still_prompt"] += " 窗边构图。"
    assert client.post(review_path + "/adopt", json=preview).is_success
    reviewed = client.get(root + f"/shots/{carrying['id']}").json()
    assert reviewed["storyboard"]["still_prompt"] == preview["still_prompt"]
    assert reviewed["prompt_override"] == "手调的构图"
    assert client.post(decision, json=payload).is_success
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
    # Reinserting the original heading restores its identity and production.
    assert client.put(path + "/blocks", json={"blocks": blocks[4:]}).is_success
    assert [s["id"] for s in client.get(path + "/scenes").json()] == [second]
    assert by_scene(first)[0]["selected_generation_id"] == generation["id"]
    assert client.put(path + "/blocks", json={"blocks": blocks[4:] + blocks[:4]}).is_success
    assert [s["id"] for s in client.get(path + "/scenes").json()] == [second, first]
    reordered = {s["shot_id"]: s for s in client.get(path + "/affected-shots").json()}
    assert reordered[later["id"]]["changes"][0]["after"] is None
