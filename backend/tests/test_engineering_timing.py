"""Exact time semantics, migration and adoption review are engineering contracts."""

# ruff: noqa: F811
import copy
import uuid
from fractions import Fraction

import pytest

from app.modules.engineering.timing import TimeMap, affected_ranges, encoded, inverse, source_at
from tests.test_engineering import accept
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def mapping():
    return {
        "duration": encoded(6),
        "source_domain": {"start": encoded(0), "end": encoded(4)},
        "audio_policy": "none",
        "segments": [
            {
                "id": str(uuid.uuid4()),
                "local": {"start": encoded(a), "end": encoded(b)},
                "source_start": encoded(source),
                "rate": encoded(rate),
                "join": "cut",
                "hold": False,
            }
            for a, b, source, rate in [(0, 2, 0, 1), (2, 4, 0, 1), (4, 6, 4, -1)]
        ],
    }


def test_exact_loop_reverse_hold_and_affected_boundaries():
    raw = mapping()
    m = TimeMap.model_validate(raw)
    assert source_at(m, Fraction(7, 3)) == Fraction(1, 3)
    assert [hit["point"] for hit in inverse(m, Fraction(1))] == [encoded(1), encoded(3)]
    assert inverse(m, Fraction(3))[0]["point"] == encoded(5)
    ranges = affected_ranges(m, Fraction(2), Fraction(3))
    assert len(ranges) == 1
    assert ranges[0]["start"] == encoded(5) and ranges[0]["end"] == encoded(6)
    assert ranges[0]["start_closed"] is False and ranges[0]["end_closed"] is False
    with pytest.raises(ValueError):
        source_at(m, Fraction(6))
    raw["segments"][2].update(rate=encoded(0), source_start=encoded(1), hold=True)
    hold = TimeMap.model_validate(raw)
    assert inverse(hold, Fraction(1))[-1]["range"] == {"start": encoded(4), "end": encoded(6)}
    assert len(inverse(hold, Fraction(1))) == 3
    broken = copy.deepcopy(raw)
    broken["segments"][1]["local"]["start"] = encoded(Fraction(5, 2))
    with pytest.raises(ValueError):
        TimeMap.model_validate(broken)
    broken = copy.deepcopy(raw)
    broken["segments"][2]["hold"] = False
    with pytest.raises(ValueError):
        TimeMap.model_validate(broken)
    broken = mapping()
    broken["segments"][1]["join"] = "continuous"
    with pytest.raises(ValueError):
        TimeMap.model_validate(broken)


@pytest.mark.parametrize("relation", ["offset", "shared_map", "semantic_alignment"])
def test_sync_hard_rejects_review_requires_explicit_adoption(studio, relation):
    client, _, _, path, _, _ = ready(studio)
    child, parent, first, second, sync_id = [str(uuid.uuid4()) for _ in range(5)]
    source_map = mapping()
    source_map["duration"] = encoded(2)
    source_map["segments"] = source_map["segments"][:1]
    payload = {
        "canvas": {"width": 16, "height": 16, "alpha": "opaque"},
        "duration": encoded(4),
        "frame_rate": encoded(24),
        "layers": [
            {
                "id": identity,
                "name": "画面",
                "source": "shared",
                "active": {"start": encoded(0), "end": encoded(2)},
                "source_map": source_map,
            }
            for identity in (first, second)
        ],
        "sync": [
            {
                "id": sync_id,
                "name": "音画起点",
                "relation": relation,
                "policy": "hard",
                "members": [
                    {"instance_id": identity, "local": encoded(0)} for identity in (first, second)
                ],
            }
        ],
    }
    command = {
        "operation": "document.put",
        "document_id": parent,
        "expected_revision": 0,
        "kind": "composition.document",
        "schema_version": 2,
        "title": "同步合成",
        "inputs": {
            "shared": {"accepts": "image.document", "target": {"candidate_document": child}}
        },
        "payload": payload,
    }
    if relation == "semantic_alignment":
        for alias in ("source_baseline", "evidence", "evidence_baseline"):
            command["inputs"][alias] = copy.deepcopy(command["inputs"]["shared"])
        constraint = payload["sync"][0]
        constraint.update(evidence_input="evidence", expected_evidence_input="evidence_baseline")
        for anchor in constraint["members"]:
            anchor["expected_source_input"] = "source_baseline"

    def propose(base, commands):
        return post_ok(
            client,
            path + "/changes",
            {"request_key": str(uuid.uuid4()), "base_revision": base, "commands": commands},
        )

    initial = propose(
        0,
        [
            command,
            {
                "operation": "document.put",
                "document_id": child,
                "expected_revision": 0,
                "kind": "image.document",
                "title": "画面",
                "publish_output": False,
                "payload": {"canvas": {"width": 16, "height": 16, "alpha": "opaque"}, "layers": []},
            },
        ],
    )
    accept(client, path, initial)
    command["expected_revision"] = 1
    for link in command["inputs"].values():
        link["target"] = {"id": child, "revision": 1}
    if relation == "semantic_alignment":
        from app.modules.engineering import native_v2

        replaced = copy.deepcopy(command["inputs"])
        replaced["shared"]["target"]["revision"] = 2
        with pytest.raises(ValueError, match="硬同步"):
            native_v2.validate("composition.document", replaced, payload)
    command["payload"]["layers"][1]["active"] = {"start": encoded(1), "end": encoded(3)}
    before = client.get(path + "/documents").json()
    rejected = client.post(
        path + "/changes",
        json={"request_key": str(uuid.uuid4()), "base_revision": 1, "commands": [command]},
    )
    assert rejected.status_code == 422
    assert client.get(path + "/documents").json() == before
    command["payload"]["sync"][0]["policy"] = "review"
    changed = propose(1, [command])
    impact = client.get(path + f"/changes/{changed['id']}/impact").json()
    assert impact["temporal_impacts"][0]["after"]["status"] == "conservative"
    assert impact["temporal_impacts"][0]["after"]["intervals"]
    issue = impact["sync_reviews"][0]
    assert issue["name"] == "音画起点"
    endpoint = path + f"/changes/{changed['id']}"
    assert (
        client.post(endpoint + "/accept", json={"fingerprint": changed["fingerprint"]}).status_code
        == 409
    )
    assert client.get(path).json()["revision"] == 1
    from tests.test_engineering_review import preview

    rendered = preview(client, path, changed)
    assert rendered["preview"]["status"] == "succeeded", rendered
    adopted = post_ok(
        client,
        endpoint + "/accept",
        {"fingerprint": changed["fingerprint"], "acknowledged_reviews": [issue["key"]]},
    )
    assert adopted["committed_revision"] == 2
    package = client.get(path + "/package").json()
    project = path.split("/works/")[0]
    work = post_ok(client, project + "/works", {"title": "同步工程迁移"})
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
    reviews = imported["snapshot"]["sync_reviews"]
    assert len(reviews) == 1 and reviews[0]["key"] != issue["key"]
    assert "来源修订已改变" not in " ".join(reviews[0]["reasons"])


def test_explicit_v1_migration_preserves_source_and_old_version(studio):
    client, _, _, path, _, _ = ready(studio)
    identity = str(uuid.uuid4())
    first = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": identity,
                    "expected_revision": 0,
                    "kind": "audio.document",
                    "title": "声音工程",
                    "payload": {"tracks": []},
                }
            ],
        },
    )
    accept(client, path, first)
    old = client.get(f"{path}/documents/{identity}/revisions/1").json()
    migrated = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {
                    "operation": "document.migrate",
                    "document_id": identity,
                    "document_revision": 1,
                    "expected_revision": 1,
                    "target_schema_version": 2,
                }
            ],
        },
    )
    ref = migrated["snapshot"]["native_roots"][identity]
    current = client.get(f"{path}/documents/{identity}/revisions/{ref['revision']}").json()
    assert current["schema_version"] == 2 and current["payload"]["sync"] == []
    assert client.get(f"{path}/documents/{identity}/revisions/1").json() == old


def test_read_only_time_lookup_returns_all_occurrences(studio):
    client, _, _, path, _, _ = ready(studio)
    parent, child, instance = [str(uuid.uuid4()) for _ in range(3)]
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": parent,
                    "expected_revision": 0,
                    "kind": "composition.document",
                    "schema_version": 2,
                    "title": "重复出现",
                    "inputs": {
                        "source": {
                            "target": {"candidate_document": child},
                            "accepts": "image.document",
                        }
                    },
                    "payload": {
                        "canvas": {"width": 16, "height": 16, "alpha": "opaque"},
                        "duration": encoded(8),
                        "frame_rate": encoded(24),
                        "layers": [
                            {
                                "id": instance,
                                "name": "循环画面",
                                "source": "source",
                                "active": {"start": encoded(2), "end": encoded(8)},
                                "source_map": mapping(),
                            }
                        ],
                    },
                },
                {
                    "operation": "document.put",
                    "document_id": child,
                    "expected_revision": 0,
                    "kind": "image.document",
                    "title": "画面",
                    "publish_output": False,
                    "payload": {
                        "canvas": {"width": 16, "height": 16, "alpha": "opaque"},
                        "layers": [],
                    },
                },
            ],
        },
    )
    query = {
        "document_id": parent,
        "revision": 1,
        "instance_id": instance,
        "direction": "to_local",
        "time": encoded(1),
    }
    result = post_ok(client, path + "/locate-time", query)
    assert result["status"] == "ambiguous_occurrence"
    assert [item["point"] for item in result["matches"]] == [encoded(3), encoded(5)]
    assert result["source_revision"] == {"id": child, "revision": 1}
    query.update(direction="to_source", time=encoded(5))
    assert post_ok(client, path + "/locate-time", query)["matches"][0]["source"] == encoded(1)
    query["time"] = encoded(8)
    assert post_ok(client, path + "/locate-time", query)["status"] == "outside_usage"
    assert client.get(path).json()["revision"] == 0
    assert change["status"] == "proposed"


def test_populated_migration_preserves_rational_source_coordinates():
    from app.modules.engineering import native_v2

    instance = str(uuid.uuid4())
    payload = {
        "tracks": [
            {
                "id": str(uuid.uuid4()),
                "name": "音轨",
                "clips": [
                    {
                        "id": instance,
                        "source": "voice",
                        "source_range": {
                            "start": encoded(Fraction(1, 3)),
                            "end": encoded(Fraction(7, 3)),
                        },
                        "start": encoded(5),
                    }
                ],
            }
        ]
    }
    _, migrated = native_v2.migrate("audio.document", {}, payload)
    clip = migrated["tracks"][0]["clips"][0]
    model = TimeMap.model_validate(clip["source_map"])
    assert source_at(model, Fraction(1, 3)) == Fraction(2, 3)
    assert model.duration.model_dump() == encoded(2)
    assert clip["start"] == encoded(5) and clip["id"] == instance


def test_old_composition_executor_rejects_new_nested_time_format():
    from app.core.errors import CapabilityUnsupported
    from app.modules.engineering.composition_executor import preflight

    with pytest.raises(CapabilityUnsupported, match="时间映射格式"):
        preflight([{"kind": "composition.document", "schema_version": 2}], {})
