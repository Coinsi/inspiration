"""Contract checks for native editing, without claiming render support."""

import copy
import uuid

import pytest

from app.modules.engineering import document_types
from app.modules.engineering.native import validate


def image():
    return {
        "canvas": {"width": 1920, "height": 1080},
        "layers": [{"id": str(uuid.uuid4()), "name": "角色", "source": "hero"}],
    }


def inputs():
    return {"hero": {"target": {"id": str(uuid.uuid4()), "revision": 1}, "accepts": "resource"}}


def test_native_types_are_not_advertised_as_renderers():
    for kind in ("image.document", "audio.document", "composition.document"):
        assert document_types.describe()[kind] == {
            "schema_version": 1 if kind == "image.document" else 2,
            "renderable": False,
        }
        assert document_types.adapter(kind, 1).renderable is False


def test_image_instances_are_independent_and_fixed():
    payload = image()
    second = copy.deepcopy(payload["layers"][0])
    second.update(id=str(uuid.uuid4()), transform={"opacity": 0.5})
    payload["layers"].append(second)
    doc = validate("image.document", inputs(), payload)
    assert doc.layers[0].transform.opacity == 1
    assert doc.layers[1].transform.opacity == 0.5


@pytest.mark.parametrize("bad", ["latest", True, 0, -1])
def test_fixed_revisions_reject_dynamic_or_coerced_numbers(bad):
    links = inputs()
    links["hero"]["target"]["revision"] = bad
    with pytest.raises(ValueError):
        validate("image.document", links, image())


def test_duplicate_instances_missing_sources_and_unknown_effects_fail():
    payload = image()
    payload["layers"].append(copy.deepcopy(payload["layers"][0]))
    with pytest.raises(ValueError):
        validate("image.document", inputs(), payload)
    with pytest.raises(ValueError):
        validate("image.document", {}, image())
    payload = image()
    payload["layers"][0]["effects"] = [{"type": "unimplemented.glow"}]
    with pytest.raises(ValueError):
        validate("image.document", inputs(), payload)


def test_audio_sample_precision_and_local_gain():
    payload = {
        "tracks": [
            {
                "id": str(uuid.uuid4()),
                "name": "对白",
                "clips": [
                    {
                        "id": str(uuid.uuid4()),
                        "source": "hero",
                        "source_range": {
                            "start": {"numerator": 1, "denominator": 48000},
                            "end": {"numerator": 24001, "denominator": 48000},
                        },
                        "start": {"numerator": 0, "denominator": 1},
                        "gain_db": -6,
                    }
                ],
            }
        ]
    }
    doc = validate("audio.document", inputs(), payload)
    assert doc.tracks[0].clips[0].source_range.start.denominator == 48000
    payload["tracks"][0]["clips"][0]["source_range"]["end"]["numerator"] = 0
    with pytest.raises(ValueError):
        validate("audio.document", inputs(), payload)


def test_composition_requires_layers_within_duration():
    payload = image()
    payload.update(
        duration={"numerator": 5, "denominator": 1},
        frame_rate={"numerator": 30000, "denominator": 1001},
    )
    payload["layers"][0].update(
        active={
            "start": {"numerator": 0, "denominator": 1},
            "end": {"numerator": 6, "denominator": 1},
        },
        source_start={"numerator": 0, "denominator": 1},
    )
    with pytest.raises(ValueError):
        validate("composition.document", inputs(), payload)
