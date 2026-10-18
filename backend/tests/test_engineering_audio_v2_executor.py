import array
import copy
import hashlib
import io
import math
import uuid
import wave

import pytest

from app.core.errors import CapabilityUnsupported
from app.modules.engineering import audio_v2_executor as audio
from app.modules.engineering import documents, executors
from app.modules.engineering.timing import encoded


def fixture_plan(tmp_path, policy, segments, tone=False):
    samples = [
        int(10000 * math.sin(2 * math.pi * 440 * i / 48000)) if tone else 1000 * (1 + i // 12000)
        for i in range(48000)
    ]
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(array.array("h", samples).tobytes())
    raw = stream.getvalue()
    source = tmp_path / "source.wav"
    source.write_bytes(raw)
    resource, root = str(uuid.uuid4()), str(uuid.uuid4())
    mapping = {
        "duration": encoded(segments[-1][1]),
        "source_domain": {"start": encoded(0), "end": encoded(1)},
        "audio_policy": policy,
        "segments": [
            {
                "id": str(uuid.uuid4()),
                "local": {"start": encoded(a), "end": encoded(b)},
                "source_start": encoded(c),
                "rate": encoded(d),
                "hold": d == 0,
            }
            for a, b, c, d in segments
        ],
    }
    nodes = [
        {
            "id": resource,
            "revision": 1,
            "schema_version": 1,
            "kind": "resource",
            "inputs": {},
            "payload": {"media_type": "audio", "blob_hash": hashlib.sha256(raw).hexdigest()},
        },
        {
            "id": root,
            "revision": 1,
            "schema_version": 2,
            "kind": "audio.document",
            "inputs": {"voice": {"target": {"id": resource, "revision": 1}, "accepts": "resource"}},
            "payload": {
                "sample_rate": 48000,
                "channels": 1,
                "tracks": [
                    {
                        "id": str(uuid.uuid4()),
                        "name": "voice",
                        "clips": [
                            {
                                "id": str(uuid.uuid4()),
                                "source": "voice",
                                "start": encoded(0),
                                "source_map": mapping,
                            }
                        ],
                    }
                ],
            },
        },
    ]
    for node in nodes:
        node["content_hash"] = documents.digest(node["inputs"], node["payload"])
    body = {"executor": audio.EXECUTOR, "root": {"id": root, "revision": 1}, "nodes": nodes}
    return {**body, "fingerprint": documents.digest({}, body)}, source


def render(plan, source):
    with executors.execute(plan, None, lambda _: source, lambda: False) as (output, kind):
        assert kind == "audio"
        with wave.open(str(output), "rb") as wav:
            assert wav.getframerate() == 48000
            result = array.array("h", wav.readframes(wav.getnframes()))
    assert not output.exists()
    return result


@pytest.mark.parametrize(
    "policy,segments,expected",
    [
        ("reverse", [(0, 1, 1, -1)], [4000, 3000, 2000, 1000]),
        ("resample", [(0, 0.5, 0, 1), (0.5, 1, 0, 1)], [1000, 2000, 1000, 2000]),
        ("mute", [(0, 1, 0.5, 0)], [0, 0, 0, 0]),
    ],
)
def test_mapped_audio_samples(tmp_path, policy, segments, expected):
    plan, source = fixture_plan(tmp_path, policy, segments)
    result = render(plan, source)
    assert len(result) == 48000
    assert [result[index] for index in (6000, 18000, 30000, 42000)] == expected


@pytest.mark.parametrize("policy,frequency", [("resample", 880), ("stretch", 440)])
def test_pitch_policy_and_exact_duration(tmp_path, policy, frequency):
    plan, source = fixture_plan(tmp_path, policy, [(0, 0.5, 0, 2)], tone=True)
    result = render(plan, source)
    assert len(result) == 24000
    middle = result[4800:19200]
    crossings = sum(a <= 0 < b for a, b in zip(middle, middle[1:], strict=False))
    measured = crossings * 48000 / len(middle)
    assert abs(measured - frequency) < 10


def test_out_of_source_and_invalid_grid_are_rejected(tmp_path):
    plan, source = fixture_plan(tmp_path, "resample", [(0, 1, 0, 1)])
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(array.array("h", [1000] * 24000).tobytes())
    resource = plan["nodes"][0]
    resource["payload"]["blob_hash"] = hashlib.sha256(source.read_bytes()).hexdigest()
    resource["content_hash"] = documents.digest(resource["inputs"], resource["payload"])
    plan["fingerprint"] = documents.digest(
        {}, {k: v for k, v in plan.items() if k != "fingerprint"}
    )
    with pytest.raises(CapabilityUnsupported, match="源声音"):
        render(plan, source)
    invalid = copy.deepcopy(plan)
    clip = invalid["nodes"][-1]["payload"]["tracks"][0]["clips"][0]
    clip["start"] = {"numerator": 1, "denominator": 7}
    with pytest.raises(CapabilityUnsupported, match="采样网格"):
        audio.preflight(invalid["nodes"], invalid["root"])


def test_nested_legacy_audio_is_fixed_and_reversed(tmp_path):
    plan, source = fixture_plan(tmp_path, "reverse", [(0, 1, 1, -1)])
    root = plan["nodes"][-1]
    child = copy.deepcopy(root)
    child["id"] = str(uuid.uuid4())
    child["schema_version"] = 1
    clip = child["payload"]["tracks"][0]["clips"][0]
    clip.pop("source_map")
    clip["source_range"] = {"start": encoded(0), "end": encoded(1)}
    child["content_hash"] = documents.digest(child["inputs"], child["payload"])
    root["inputs"]["voice"] = {
        "target": {"id": child["id"], "revision": 1},
        "accepts": "audio.document",
    }
    root["content_hash"] = documents.digest(root["inputs"], root["payload"])
    plan["nodes"].insert(1, child)
    plan["fingerprint"] = documents.digest(
        {}, {k: v for k, v in plan.items() if k != "fingerprint"}
    )
    result = render(plan, source)
    assert len(result) == 48000
    assert [result[index] for index in (6000, 18000, 30000, 42000)] == [4000, 3000, 2000, 1000]


def test_tiny_stretch_does_not_silently_become_silence(tmp_path):
    from fractions import Fraction

    plan, source = fixture_plan(tmp_path, "stretch", [(0, Fraction(1, 1000), 0, 2)], tone=True)
    with pytest.raises(CapabilityUnsupported):
        render(plan, source)


def test_cancellation_and_pinned_hash(tmp_path):
    from app.core.errors import Conflict
    from app.modules.generation.media_engine import Canceled

    plan, source = fixture_plan(tmp_path, "resample", [(0, 1, 0, 1)])
    with pytest.raises(Canceled):
        with executors.execute(plan, None, lambda _: source, lambda: True):
            pytest.fail("Canceled plan must not produce media")
    source.write_bytes(source.read_bytes() + b"tampered")
    with pytest.raises(Conflict, match="校验"):
        render(plan, source)
