# ruff: noqa: F811
import copy
import uuid

import pytest

from app.modules.engineering.timing import encoded
from tests.test_engineering import accept
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.mark.parametrize("relation", ["offset", "shared_map", "semantic_alignment"])
@pytest.mark.parametrize("policy", ["hard", "review"])
@pytest.mark.parametrize("second_kind", ["composition.document", "audio.document"])
def test_cross_document_sync_atomic_changes_and_migration(studio, policy, second_kind, relation):
    client, _, _, path, _, _ = ready(studio)
    source, first, second, sync, instance, constraint = [str(uuid.uuid4()) for _ in range(6)]

    def link(identity, candidate=True):
        return {
            "target": {"candidate_document": identity}
            if candidate
            else {"id": identity, "revision": 1},
            "accepts": "composition.document",
        }

    def put(identity, kind, payload, inputs, version=1, published=True):
        return {
            "operation": "document.put",
            "document_id": identity,
            "expected_revision": 0,
            "kind": kind,
            "schema_version": version,
            "title": kind,
            "inputs": inputs,
            "payload": payload,
            "publish_output": published,
        }

    def propose(base, commands):
        return client.post(
            path + "/changes",
            json={"request_key": str(uuid.uuid4()), "base_revision": base, "commands": commands},
        )

    mapping = {
        "duration": encoded(2),
        "source_domain": {"start": encoded(0), "end": encoded(2)},
        "audio_policy": "none",
        "segments": [
            {
                "id": str(uuid.uuid4()),
                "local": {"start": encoded(0), "end": encoded(2)},
                "source_start": encoded(0),
                "rate": encoded(1),
            }
        ],
    }
    content = {
        "canvas": {"width": 16, "height": 16, "alpha": "opaque"},
        "duration": encoded(4),
        "frame_rate": encoded(2),
        "layers": [
            {
                "id": instance,
                "name": "画面",
                "source": "image",
                "active": {"start": encoded(0), "end": encoded(2)},
                "source_map": mapping,
            }
        ],
    }
    image_input = {"image": {"target": {"candidate_document": source}, "accepts": "image.document"}}
    commands = [
        put(
            source,
            "image.document",
            {"canvas": {"width": 16, "height": 16}, "layers": []},
            {},
            published=False,
        ),
        put(first, "composition.document", copy.deepcopy(content), image_input, 2),
        put(second, "composition.document", copy.deepcopy(content), image_input, 2),
        put(
            sync,
            "sync.document",
            {
                "constraints": [
                    {
                        "id": constraint,
                        "name": "跨输出同步",
                        "relation": relation,
                        "policy": policy,
                        "members": [
                            {"document_input": alias, "instance_id": instance, "local": encoded(0)}
                            for alias in ("picture", "voice")
                        ],
                    }
                ]
            },
            {"picture": link(first), "voice": link(second)},
        ),
    ]
    sound_source = str(uuid.uuid4())
    if second_kind == "audio.document":
        audio_mapping = copy.deepcopy(mapping)
        audio_mapping["audio_policy"] = "resample"
        commands[2] = put(
            second,
            "audio.document",
            {
                "tracks": [
                    {
                        "id": str(uuid.uuid4()),
                        "name": "声音",
                        "clips": [
                            {
                                "id": instance,
                                "source": "image",
                                "start": encoded(0),
                                "source_map": audio_mapping,
                            }
                        ],
                    }
                ]
            },
            {
                "image": {
                    "target": {"candidate_document": sound_source},
                    "accepts": "audio.document",
                }
            },
            2,
        )
        commands[3]["inputs"]["voice"]["accepts"] = "audio.document"
        commands.append(put(sound_source, "audio.document", {"tracks": []}, {}, published=False))
    if relation == "semantic_alignment":
        rule = commands[3]["payload"]["constraints"][0]
        for member in rule["members"]:
            alias = member["document_input"]
            baseline = alias + "_baseline"
            member["expected_source_input"] = baseline
            commands[3]["inputs"][baseline] = copy.deepcopy(
                commands[1 if alias == "picture" else 2]["inputs"]["image"]
            )
        for alias in ("evidence", "evidence_baseline"):
            commands[3]["inputs"][alias] = copy.deepcopy(commands[1]["inputs"]["image"])
        rule.update(evidence_input="evidence", expected_evidence_input="evidence_baseline")
    initial = propose(0, commands)
    assert initial.status_code == 200, initial.text
    accept(client, path, initial.json())
    changed = copy.deepcopy(commands[2])
    changed["expected_revision"] = 1
    changed["inputs"]["image"]["target"] = {
        "id": sound_source if second_kind == "audio.document" else source,
        "revision": 1,
    }
    if second_kind == "audio.document":
        changed["payload"]["tracks"][0]["clips"][0]["start"] = encoded(1)
    else:
        changed["payload"]["layers"][0]["active"] = {"start": encoded(1), "end": encoded(3)}
    before = client.get(path + "/documents").json()
    result = propose(1, [changed])
    if policy == "hard":
        assert result.status_code == 422, result.text
        assert client.get(path + "/documents").json() == before
        # Updating the binding alone cannot bypass the actual timing check.
        updated_sync = copy.deepcopy(commands[3])
        updated_sync["expected_revision"] = 1
        if relation == "semantic_alignment":
            for alias in ("picture_baseline", "voice_baseline", "evidence", "evidence_baseline"):
                current_source = (
                    sound_source
                    if alias == "voice_baseline" and second_kind == "audio.document"
                    else source
                )
                updated_sync["inputs"][alias]["target"] = {"id": current_source, "revision": 1}

        updated_sync["inputs"]["picture"] = link(first, False)
        assert propose(1, [changed, updated_sync]).status_code == 422
        other = copy.deepcopy(commands[1])
        other["expected_revision"] = 1
        other["inputs"]["image"]["target"] = {"id": source, "revision": 1}
        other["payload"]["layers"][0]["active"] = {"start": encoded(1), "end": encoded(3)}
        updated_sync["inputs"]["picture"] = link(first)
        result = propose(1, [changed, other, updated_sync])
        assert result.status_code == 200, result.text
        assert result.json()["snapshot"]["sync_reviews"] == []
        accept(client, path, result.json())
    else:
        assert result.status_code == 200, result.text
        candidate = result.json()
        issues = candidate["snapshot"]["sync_reviews"]
        assert len(issues) == 1
        assert any("修订" in reason for reason in issues[0]["reasons"])
        assert any("间距" in reason for reason in issues[0]["reasons"])
        endpoint = path + f"/changes/{candidate['id']}/accept"
        assert (
            client.post(endpoint, json={"fingerprint": candidate["fingerprint"]}).status_code == 409
        )
        post_ok(
            client,
            endpoint,
            {"fingerprint": candidate["fingerprint"], "acknowledged_reviews": [issues[0]["key"]]},
        )
    package = client.get(path + "/package").json()
    project = path.split("/works/")[0]
    work = post_ok(client, project + "/works", {"title": "跨文档同步迁移"})
    destination = project + f"/works/{work['id']}/engineering"
    imported = post_ok(
        client,
        destination + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [{"operation": "package.import", "package": package}],
        },
    )
    assert len(imported["snapshot"]["sync_reviews"]) == (1 if policy == "review" else 0)


def test_sync_budget_and_alias_duplicates(monkeypatch):
    from app.core.errors import CapabilityUnsupported
    from app.modules.engineering import work_sync

    a, b, sync, anchor = [str(uuid.uuid4()) for _ in range(4)]
    inputs = {
        alias: {"target": {"id": identity, "revision": 1}, "accepts": "composition.document"}
        for alias, identity in (("a", a), ("b", b))
    }
    payload = {
        "constraints": [
            {
                "id": str(uuid.uuid4()),
                "name": "同步",
                "relation": "offset",
                "policy": "hard",
                "members": [
                    {"document_input": alias, "instance_id": anchor, "local": encoded(0)}
                    for alias in ("a", "b")
                ],
            }
        ]
    }
    duplicate = copy.deepcopy(inputs)
    duplicate["b"]["target"] = duplicate["a"]["target"]
    with pytest.raises(ValueError, match="别名"):
        work_sync.validate(duplicate, payload)
    node = {
        "id": sync,
        "revision": 1,
        "schema_version": 1,
        "kind": "sync.document",
        "title": "同步",
        "inputs": inputs,
        "payload": payload,
    }
    manifest = {
        "id": str(uuid.uuid4()),
        "revision": 1,
        "kind": "work.manifest",
        "inputs": {"sync": {"target": {"id": sync, "revision": 1}}},
        "payload": {"outputs": {sync: {"input": "sync"}}},
    }
    monkeypatch.setattr(work_sync, "MAX_MEMBERS", 1)
    with pytest.raises(CapabilityUnsupported, match="预算"):
        work_sync.review_items([node, manifest])
