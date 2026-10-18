"""Validate actual frame samples and persisted rendering for mapped compositions."""

import copy
import io
import uuid
from fractions import Fraction

import pytest
from PIL import Image

from app.core.errors import CapabilityUnsupported
from app.modules.engineering import composition_v2_executor as comp
from app.modules.engineering import documents, executors, image_executor, native_v2
from tests.test_engineering_composition_executor import example, time
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def mapped(mode):
    plan, raw = example()
    root = plan["nodes"][-1]
    _, root["payload"] = native_v2.migrate(root["kind"], root["inputs"], root["payload"])
    root["schema_version"] = 2
    layer = root["payload"]["layers"][0]
    layer["active"] = {"start": time(0), "end": time(2)}
    segments = []

    def segment(start, end, source, rate, hold=False):
        segments.append(
            {
                "id": str(uuid.uuid4()),
                "local": {"start": time(start, 2), "end": time(end, 2)},
                "source_start": time(source, 2),
                "rate": time(rate),
                "hold": hold,
            }
        )

    if mode == "reverse":
        segment(0, 4, 4, -1)
    elif mode == "hold":
        segment(0, 4, 1, 0, True)
    else:
        segment(0, 2, 0, 1)
        segment(2, 4, 0, 1)
    layer["source_map"] = {
        "duration": time(2),
        "source_domain": {"start": time(0), "end": time(2)},
        "audio_policy": "none",
        "segments": segments,
    }
    root["content_hash"] = documents.digest(root["inputs"], root["payload"])
    plan["executor"] = comp.EXECUTOR
    plan["fingerprint"] = documents.digest(
        {}, {k: v for k, v in plan.items() if k != "fingerprint"}
    )
    return plan, raw


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("reverse", [False, False, True, False]),
        ("repeat", [False, True, False, True]),
        ("hold", [True, True, True, True]),
    ],
)
def test_exact_frames_and_encoded_output(mode, expected):
    plan, raw = mapped(mode)
    comp.preflight(plan["nodes"], plan["root"])
    colors = []
    for frame in range(4):
        png = image_executor.render(comp.frame_plan(plan, Fraction(frame, 2)), lambda _: raw)
        colors.append(Image.open(io.BytesIO(png)).getpixel((8, 8))[0] == 255)
    assert colors == expected
    with executors.execute(plan, lambda _: raw, None, lambda: False) as (output, kind):
        assert kind == "video" and b"ftyp" in output.read_bytes()[:20]
        from app.modules.generation import media_engine

        decoded = output.parent / "decoded-%02d.png"
        media_engine.run(["-i", str(output), str(decoded)], timeout=30)
        actual = [
            Image.open(frame).getpixel((8, 8))[0] > 200
            for frame in sorted(output.parent.glob("decoded-*.png"))
        ]
        assert actual == expected
    assert not output.exists()


def test_reject_source_domain_and_audio_policy():
    plan, _ = mapped("reverse")
    invalid = copy.deepcopy(plan)
    invalid["nodes"][-1]["payload"]["layers"][0]["source_map"]["source_domain"]["end"] = time(3)
    with pytest.raises(CapabilityUnsupported):
        comp.preflight(invalid["nodes"], invalid["root"])
    plan["nodes"][-1]["payload"]["layers"][0]["source_map"]["audio_policy"] = "reverse"
    with pytest.raises(CapabilityUnsupported):
        comp.preflight(plan["nodes"], plan["root"])


def test_persisted_v2_preview(studio):  # noqa: F811
    from tests.test_engineering_review import preview, ready

    client, _, _, path, _, _ = ready(studio)
    plan, _ = mapped("repeat")
    child, root = plan["nodes"][1:]
    # Blank child still exercises a persisted nested time-map and mixed schemas.
    child["payload"]["layers"] = []
    commands = []
    for n in (child, root):
        commands.append(
            {
                "operation": "document.put",
                "document_id": n["id"],
                "expected_revision": 0,
                "kind": n["kind"],
                "schema_version": n["schema_version"],
                "title": "Mapped composition",
                "inputs": {},
                "payload": n["payload"],
            }
        )
    commands[0]["publish_output"] = False
    commands[1]["inputs"] = {
        "source": {"accepts": "composition.document", "target": {"candidate_document": child["id"]}}
    }
    change = post_ok(
        client,
        path + "/changes",
        {"request_key": str(uuid.uuid4()), "base_revision": 0, "commands": commands},
    )
    rendered = preview(client, path, change)
    assert rendered["preview"]["status"] == "succeeded", rendered
    assert rendered["preview"]["output_type"] == "video"
    assert client.get(path).json()["revision"] == 0
