"""Late candidate preservation and conservative replay against real isolated DB."""

# ruff: noqa: F811
import copy
import uuid

from tests.test_engineering import accept, request, setup
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def put(identity=None, revision=0, width=16):
    return {
        "operation": "document.put",
        "document_id": identity or str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "image.document",
        "title": "画面候选",
        "payload": {"canvas": {"width": width, "height": 16}, "layers": []},
    }


def propose(client, path, base, commands, **extra):
    return post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": base,
            "commands": commands,
            **extra,
        },
    )


def rebase(client, path, change, revision, key=None):
    return client.post(
        path + f"/changes/{change['id']}/rebase",
        json={
            "fingerprint": change["fingerprint"],
            "request_key": key or str(uuid.uuid4()),
            "expected_revision": revision,
        },
    )


def test_late_resource_candidate_preserved_rebased_and_reviewed(studio):
    client, _, _, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    user = post_ok(client, path + "/changes", request(source))
    accept(client, path, user)
    provenance = {
        name: str(uuid.uuid4()) for name in ("run_id", "task_id", "attempt_id", "workspace_id")
    }
    late = post_ok(client, path + "/changes", {**request(source), "provenance": provenance})
    assert late["base_revision"] == 0 and late["status"] == "proposed"
    assert late["provenance"] == provenance
    assert late["canonical_commands"][0]["operation"] == "resource.add"
    assert late["write_scopes"] and late["read_preconditions"]
    assert (
        client.post(
            path + f"/changes/{late['id']}/accept", json={"fingerprint": late["fingerprint"]}
        ).status_code
        == 409
    )
    key = str(uuid.uuid4())
    response = rebase(client, path, late, 1, key)
    assert response.status_code == 200, response.text
    merged = response.json()
    assert merged["id"] != late["id"] and merged["rebased_from_id"] == late["id"]
    assert len(merged["snapshot"]["resources"]) == 2
    assert client.get(path).json()["revision"] == 1
    accept(client, path, merged)
    assert rebase(client, path, late, 1, key).json()["id"] == merged["id"]
    assert client.get(path + f"/changes/{late['id']}").json()["status"] == "proposed"
    assert len(client.get(path).json()["snapshot"]["resources"]) == 2
    other = post_ok(client, path + "/changes", request(source))
    assert rebase(client, path, other, 1, key).status_code == 409


def test_same_usage_conflict_is_retained_without_overwrite(studio):
    client, _, _, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    data = request(source)
    a = post_ok(client, path + "/changes", data)
    accept(client, path, a)
    data["request_key"] = str(uuid.uuid4())
    data["commands"][0]["name"] = "晚到的不同名称"
    late = post_ok(client, path + "/changes", data)
    failed = rebase(client, path, late, 1)
    assert failed.status_code == 409
    assert failed.json()["error"]["detail"]["conflicts"][0]["reason"] == "read_changed"
    assert client.get(path).json()["snapshot"]["resources"][0]["name"] == "片头参考"


def test_independent_native_changes_merge_but_same_document_conflicts(studio):
    client, _, _, root, wid, _ = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    a, b = put(), put()
    initial = propose(client, path, 0, [a, b])
    accept(client, path, initial)
    user = propose(client, path, 1, [put(b["document_id"], 1, 32)])
    accept(client, path, user)
    late = propose(client, path, 1, [put(a["document_id"], 1, 64)])
    merged = rebase(client, path, late, 2)
    assert merged.status_code == 200, merged.text
    accept(client, path, merged.json())
    roots = client.get(path).json()["snapshot"]["native_roots"]
    for command, width in ((a, 64), (b, 32)):
        ref = roots[command["document_id"]]
        fixed = client.get(path + f"/documents/{ref['id']}/revisions/{ref['revision']}").json()
        assert fixed["payload"]["canvas"]["width"] == width
    another = propose(client, path, 1, [put(a["document_id"], 1, 128)])
    assert rebase(client, path, another, 3).status_code == 409


def test_input_dependency_change_blocks_other_output_replay(studio):
    client, _, _, root, wid, _ = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    source = put()
    accept(client, path, propose(client, path, 0, [source]))
    new_output = put()
    new_output["inputs"] = {
        "subject": {
            "target": {"id": source["document_id"], "revision": 1},
            "accepts": "image.document",
        }
    }
    new_output["payload"]["layers"] = [
        {"id": str(uuid.uuid4()), "name": "人物", "source": "subject"}
    ]
    late = propose(client, path, 1, [new_output])
    assert any("input_dependency" in read["origins"] for read in late["read_preconditions"])
    accept(client, path, propose(client, path, 1, [put(source["document_id"], 1, 32)]))
    failed = rebase(client, path, late, 2)
    assert failed.status_code == 409
    assert any(
        c["id"] == source["document_id"] for c in failed.json()["error"]["detail"]["conflicts"]
    )


def test_declared_context_dependency_cannot_be_dropped_during_replay(studio):
    client, _, _, root, wid, _ = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    context = put()
    accept(client, path, propose(client, path, 0, [context]))
    late = propose(
        client,
        path,
        1,
        [put()],
        read_preconditions=[
            {
                "kind": "document",
                "id": context["document_id"],
                "revision": 1,
            }
        ],
    )
    accept(client, path, propose(client, path, 1, [put(context["document_id"], 1, 32)]))
    assert rebase(client, path, late, 2).status_code == 409
    bad = client.post(
        path + "/changes",
        json={
            "request_key": str(uuid.uuid4()),
            "base_revision": 2,
            "commands": [put()],
            "read_preconditions": [{"kind": "work", "id": str(uuid.uuid4()), "revision": 2}],
        },
    )
    assert bad.status_code == 409


def test_late_timeline_retains_old_input_and_cannot_replace_manual_edits(studio):
    client, _, _, path, tid, url = ready(studio)
    baseline = client.get(url).json()
    manual = copy.deepcopy(baseline)
    manual["items"][0]["note"] = "手工确认的画面"
    manual["items"][0]["duration_ms"] = 900
    saved = client.put(url, json=manual)
    assert saved.status_code == 200, saved.text
    late_doc = copy.deepcopy(baseline)
    late_doc["items"][0]["duration_ms"] = 700
    late = propose(
        client, path, 0, [{"operation": "timeline.edit", "timeline_id": tid, "document": late_doc}]
    )
    assert late["timeline_update"]["expected_revision"] == baseline["revision"]
    assert late["timeline_update"]["document"]["items"][0]["duration_ms"] == 700
    assert (
        client.post(
            path + f"/changes/{late['id']}/accept", json={"fingerprint": late["fingerprint"]}
        ).status_code
        == 409
    )
    assert rebase(client, path, late, 0).status_code == 409
    current = client.get(url).json()
    assert current["items"][0]["note"] == "手工确认的画面"
    assert current["items"][0]["duration_ms"] == 900


def test_timed_document_requires_explicit_integration_review(studio):
    client, _, _, root, wid, _ = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    timed = {
        "operation": "document.put",
        "document_id": str(uuid.uuid4()),
        "expected_revision": 0,
        "kind": "audio.document",
        "title": "配音候选",
        "payload": {"tracks": []},
    }
    late = propose(client, path, 0, [timed])
    accept(client, path, propose(client, path, 0, [put()]))
    failed = rebase(client, path, late, 1)
    assert failed.status_code == 409
    assert failed.json()["error"]["detail"]["reason"] == "temporal_review_required"


def test_timeline_read_dependency_checked_even_without_work_revision_change(studio):
    client, _, _, path, tid, url = ready(studio)
    original = client.get(url).json()
    candidate = propose(
        client,
        path,
        0,
        [put()],
        read_preconditions=[
            {
                "kind": "timeline",
                "id": tid,
                "revision": original["revision"],
            }
        ],
    )
    original["items"][0]["note"] = "此时手动修改"
    assert client.put(url, json=original).status_code == 200
    assert client.get(path).json()["revision"] == 0
    failed = client.post(
        path + f"/changes/{candidate['id']}/accept", json={"fingerprint": candidate["fingerprint"]}
    )
    assert failed.status_code == 409
    assert failed.json()["error"]["detail"]["conflicts"][0]["kind"] == "timeline"


def test_declared_reads_survive_successful_replay_and_future_changes(studio):
    client, _, _, root, wid, _ = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    context = put()
    accept(client, path, propose(client, path, 0, [context]))
    late = propose(
        client,
        path,
        1,
        [put()],
        read_preconditions=[
            {
                "kind": "document",
                "id": context["document_id"],
                "revision": 1,
            }
        ],
    )
    accept(client, path, propose(client, path, 1, [put()]))
    merged = rebase(client, path, late, 2)
    assert merged.status_code == 200, merged.text
    read = next(r for r in merged.json()["read_preconditions"] if "declared" in r["origins"])
    assert read["fixed_references"][0]["revision"] == 1
    assert read["fixed_references"][0]["content_hash"]
    assert read["declared_revisions"] == [1]
    accept(client, path, propose(client, path, 2, [put(context["document_id"], 1, 32)]))
    assert rebase(client, path, merged.json(), 3).status_code == 409
