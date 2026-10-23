"""Real engineering revisions, exact feedback locations and protected scoped undo."""

# ruff: noqa: F811
import copy
import uuid

import pytest

from app.core.errors import CapabilityUnsupported, Conflict, Forbidden
from app.models.engineering import EngineeringChange
from app.models.work import CreativeWork
from app.modules.engineering import documents, instances, service
from app.modules.engineering.timing import encoded
from app.modules.harness import feedback_tools as tools
from app.modules.tools import capabilities, registry
from app.modules.tools.contracts import ToolContext
from app.modules.tools.invocation import invoke
from tests.test_engineering import accept
from tests.test_engineering_review import ready
from tests.test_engineering_timing import mapping
from tests.test_harness_context import context
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.fixture(autouse=True)
def register(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    tools.register_tools()


def propose(client, path, base, commands):
    return post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": base,
            "commands": commands,
        },
    )


def tc(work_id, request_key=None):
    return ToolContext(
        work_id=work_id,
        input_context={
            "execution": {
                "request_key": str(request_key or uuid.uuid4()),
            }
        },
    )


def image_doc(identity, width=16, expected=0):
    return {
        "operation": "document.put",
        "document_id": identity,
        "expected_revision": expected,
        "kind": "image.document",
        "title": "画面",
        "publish_output": True,
        "payload": {"canvas": {"width": width, "height": 16}, "layers": []},
    }


def native_fixture(studio, timed=False):
    client, sessions, ids, path, _, _ = ready(studio)
    work_id = uuid.UUID(path.split("/works/")[1].split("/")[0])
    parent, child, first, second = [str(uuid.uuid4()) for _ in range(4)]
    payload = {
        "canvas": {"width": 16, "height": 16},
        "layers": [
            {"id": identity, "name": "开场" if index == 0 else "结尾", "source": "shared"}
            for index, identity in enumerate([first, second])
        ],
    }
    if timed:
        payload.update(duration=encoded(20), frame_rate=encoded(24), sync=[])
        for index, layer in enumerate(payload["layers"]):
            layer.update(
                active={"start": encoded(index * 10), "end": encoded(index * 10 + 6)},
                source_map=mapping(),
            )
    owner = {
        "operation": "document.put",
        "document_id": parent,
        "expected_revision": 0,
        "kind": "composition.document" if timed else "image.document",
        "schema_version": 2 if timed else 1,
        "title": "双实例",
        "payload": payload,
        "inputs": {
            "shared": {"accepts": "image.document", "target": {"candidate_document": child}}
        },
    }
    initial = propose(client, path, 0, [owner, image_doc(child)])
    accept(client, path, initial)
    return client, sessions, ids, path, work_id, parent, child, first, second, owner


def test_feedback_loop_reverse_range_and_old_revision_never_follows_current(studio):
    client, sessions, ids, path, wid, parent, child, first, _, owner = native_fixture(
        studio, timed=True
    )
    with sessions() as db:
        args = tools.FeedbackArgs(
            work_id=wid,
            document_id=parent,
            revision=1,
            instance_id=first,
            space="source",
            time=encoded(1),
        )
        result = tools.feedback(db, context(db, ids), tc(wid), args)
        assert result["status"] == "ambiguous_occurrence"
        assert [m["document_time"] for m in result["matches"]] == [encoded(1), encoded(3)]
        reverse = tools.feedback(
            db,
            context(db, ids),
            tc(wid),
            args.model_copy(
                update={
                    "space": "document",
                    "time": None,
                    "range": tools.Span.model_validate({"start": encoded(4), "end": encoded(5)}),
                }
            ),
        )
        match = reverse["matches"][0]
        assert match["source_range"] == {"start": encoded(3), "end": encoded(4)}
        assert match["start_closed"] is False and match["end_closed"] is True
        assert result["read_preconditions"][0]["revision"] == 1
    owner["expected_revision"] = 1
    owner["inputs"]["shared"]["target"] = {"id": child, "revision": 1}
    owner["payload"]["layers"][0]["active"] = {"start": encoded(2), "end": encoded(8)}
    newer = propose(client, path, 1, [owner])
    new_revision = newer["snapshot"]["native_roots"][parent]["revision"]
    with sessions() as db:
        old = tools.feedback(db, context(db, ids), tc(wid), args)
        new = tools.feedback(
            db, context(db, ids), tc(wid), args.model_copy(update={"revision": new_revision})
        )
        assert old == result
        assert [m["document_time"] for m in new["matches"]] == [encoded(3), encoded(5)]
        assert new["current_revision_assumed"] is False


def test_sequence_overlap_returns_both_occurrences_and_still_frame_is_not_video_time(studio):
    client, sessions, ids, path, tid, url = ready(studio)
    wid = uuid.UUID(path.split("/works/")[1].split("/")[0])
    doc = client.get(url).json()
    doc["items"][0]["duration_ms"] = 2000
    doc["items"][0]["transition"] = {"type": "dissolve", "duration_ms": 500}
    doc["items"].append(
        {**copy.deepcopy(doc["items"][0]), "id": str(uuid.uuid4()), "transition": None}
    )
    change = propose(
        client, path, 0, [{"operation": "timeline.edit", "timeline_id": tid, "document": doc}]
    )
    ref = change["snapshot"]["document_roots"][tid]
    with sessions() as db:
        args = tools.FeedbackArgs(
            work_id=wid, document_id=ref["id"], revision=ref["revision"], time=encoded(1.75)
        )
        result = tools.feedback(db, context(db, ids), tc(wid), args)
        assert result["status"] == "ambiguous_instance" and len(result["matches"]) == 2
        assert {m["instance_id"] for m in result["matches"]} == {d["id"] for d in doc["items"]}
        assert all(m["source_time"] == encoded(0) for m in result["matches"])
        inverse = tools.feedback(
            db,
            context(db, ids),
            tc(wid),
            tools.FeedbackArgs(
                work_id=wid,
                document_id=ref["id"],
                revision=ref["revision"],
                instance_id=doc["items"][1]["id"],
                space="source",
                time=encoded(0),
            ),
        )
        assert inverse["status"] == "ambiguous_occurrence"
        assert inverse["matches"][0]["document_range"] == {
            "start": encoded(1.5),
            "end": encoded(3.5),
        }


def test_static_image_location_and_work_scope_use_real_tool_contract(studio):
    _, sessions, ids, _, wid, parent, _, first, _, _ = native_fixture(studio)
    with sessions() as db:
        args = {"work_id": str(wid), "document_id": parent, "revision": 1, "instance_id": first}
        receipt = invoke(db, context(db, ids), tc(wid), "engineering.feedback", args)
        assert receipt.status == "succeeded" and receipt.data["status"] == "located"
        assert receipt.data["matches"][0]["space"] == "static"
        with pytest.raises(Forbidden):
            tools.feedback(db, context(db, ids), tc(uuid.uuid4()), tools.FeedbackArgs(**args))
        with pytest.raises(CapabilityUnsupported):
            tools.feedback(
                db, context(db, ids), tc(wid), tools.FeedbackArgs(**args, time=encoded(0))
            )


def test_nested_instance_path_uses_each_fixed_time_map(studio):
    client, sessions, ids, path, wid, parent, _, first, _, _ = native_fixture(studio, timed=True)
    root, layer = str(uuid.uuid4()), str(uuid.uuid4())
    raw = mapping()
    raw["duration"], raw["segments"] = encoded(2), raw["segments"][:1]
    created = propose(
        client,
        path,
        1,
        [
            {
                "operation": "document.put",
                "document_id": root,
                "expected_revision": 0,
                "kind": "composition.document",
                "schema_version": 2,
                "title": "嵌套画面",
                "inputs": {
                    "child": {
                        "accepts": "composition.document",
                        "target": {"id": parent, "revision": 1},
                    }
                },
                "payload": {
                    "canvas": {"width": 16, "height": 16},
                    "duration": encoded(6),
                    "frame_rate": encoded(24),
                    "layers": [
                        {
                            "id": layer,
                            "name": "嵌套位置",
                            "source": "child",
                            "active": {"start": encoded(2), "end": encoded(4)},
                            "source_map": raw,
                        }
                    ],
                    "sync": [],
                },
            }
        ],
    )
    ref = created["snapshot"]["native_roots"][root]
    with sessions() as db:
        result = tools.feedback(
            db,
            context(db, ids),
            tc(wid),
            tools.FeedbackArgs(
                work_id=wid,
                document_id=root,
                revision=ref["revision"],
                instance_path=[layer, first],
                time=encoded(3),
            ),
        )
        assert result["status"] == "located"
        assert result["document_id"] == root and result["mapped_document_id"] == parent
        assert result["matches"][0]["source_time"] == encoded(1)
        assert [step["document_id"] for step in result["instance_path"]] == [root, parent]
        assert {read["id"] for read in result["read_preconditions"]} >= {root, parent}


def changed_both(studio):
    data = native_fixture(studio)
    client, _, _, path, _, parent, child, first, second, _ = data
    update = propose(
        client,
        path,
        1,
        [
            image_doc(child, width=24, expected=1),
            {
                "operation": "instance.retarget",
                "document_id": parent,
                "expected_revision": 1,
                "instances": [
                    {
                        "instance_id": i,
                        "expected": {"id": child, "revision": 1},
                        "target": {"candidate_document": child},
                    }
                    for i in (first, second)
                ],
            },
        ],
    )
    return data, update


def test_select_one_candidate_reference_and_undo_preserves_other_manual_edits(studio):
    data, original = changed_both(studio)
    client, sessions, ids, path, wid, parent, child, first, second, _ = data
    with sessions() as db:
        args = tools.SelectiveArgs(
            work_id=wid,
            base_revision=1,
            change_id=original["id"],
            fingerprint=original["fingerprint"],
            mode="select",
            selections=[
                {
                    "document_id": parent,
                    "expected_revision": 1,
                    "instance_id": first,
                }
            ],
        )
        request = tc(wid)
        receipt = invoke(
            db,
            context(db, ids),
            request,
            "engineering.selective_edit",
            args.model_dump(mode="json"),
        )
        assert receipt.status == "succeeded", receipt
        selected = receipt.data
        assert tools.selective_edit(db, context(db, ids), request, args) == selected
        assert db.get(CreativeWork, wid).engineering_revision == 1 and not selected["adopted"]
        db.commit()
    selected_detail = client.get(path + f"/changes/{selected['change_id']}").json()
    ref = selected_detail["snapshot"]["native_roots"][parent]
    with sessions() as db:
        targets = tools._references(db, wid, ref)
        assert targets[(first, "source")]["revision"] == 2
        assert targets[(second, "source")]["revision"] == 1
    accept(client, path, {"id": selected["change_id"], "fingerprint": selected["fingerprint"]})
    doc = client.get(f"{path}/documents/{parent}/revisions/{ref['revision']}").json()
    doc["payload"]["layers"][1].setdefault("transform", {})["x"] = 7
    manual = propose(
        client,
        path,
        2,
        [
            {
                "operation": "document.put",
                "document_id": parent,
                "expected_revision": ref["revision"],
                "kind": doc["kind"],
                "title": "人工调整结尾",
                "inputs": doc["inputs"],
                "payload": doc["payload"],
            }
        ],
    )
    accept(client, path, manual)
    current = manual["snapshot"]["native_roots"][parent]
    with sessions() as db:
        args = tools.SelectiveArgs(
            work_id=wid,
            base_revision=3,
            change_id=selected["change_id"],
            fingerprint=selected["fingerprint"],
            mode="undo",
            selections=[
                {
                    "document_id": parent,
                    "expected_revision": current["revision"],
                    "instance_id": first,
                }
            ],
        )
        undo = tools.selective_edit(db, context(db, ids), tc(wid), args)
        change = service.change_out(db.get(EngineeringChange, uuid.UUID(undo["change_id"])))
        fixed = change["snapshot"]["native_roots"][parent]
        _, revision = documents.read_revision(db, wid, uuid.UUID(parent), fixed["revision"])
        refs = instances.references(
            {
                "kind": "image.document",
                "schema_version": 1,
                "inputs": revision.inputs,
                "payload": revision.payload,
            }
        )
        assert refs[(first, "source")] == {"id": child, "revision": 1}
        assert revision.payload["layers"][1]["transform"]["x"] == 7
        assert db.get(CreativeWork, wid).engineering_revision == 3


def test_undo_refuses_to_clobber_new_reference_and_selection_cannot_apply_unrelated_parameter(
    studio,
):
    data, original = changed_both(studio)
    client, sessions, ids, path, wid, parent, child, first, _, _ = data
    accept(client, path, original)
    current = original["snapshot"]["native_roots"][parent]
    manual = propose(
        client,
        path,
        2,
        [
            image_doc(child, width=32, expected=2),
            {
                "operation": "instance.retarget",
                "document_id": parent,
                "expected_revision": current["revision"],
                "instances": [
                    {
                        "instance_id": first,
                        "expected": {"id": child, "revision": 2},
                        "target": {"candidate_document": child},
                    }
                ],
            },
        ],
    )
    accept(client, path, manual)
    with sessions() as db:
        args = tools.SelectiveArgs(
            work_id=wid,
            base_revision=3,
            change_id=original["id"],
            fingerprint=original["fingerprint"],
            mode="undo",
            selections=[
                {
                    "document_id": parent,
                    "expected_revision": manual["snapshot"]["native_roots"][parent]["revision"],
                    "instance_id": first,
                }
            ],
        )
        with pytest.raises(Conflict, match="其他修改"):
            tools.selective_edit(db, context(db, ids), tc(wid), args)
        assert db.get(CreativeWork, wid).engineering_revision == 3
