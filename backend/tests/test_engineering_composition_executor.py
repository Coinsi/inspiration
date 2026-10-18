"""Check actual frame boundaries and nested source time, then encode a real MP4."""

import hashlib
import io
import uuid
from fractions import Fraction

import pytest
from PIL import Image

from app.core.errors import CapabilityUnsupported
from app.modules.engineering import (
    composition_executor as comp,
)
from app.modules.engineering import (
    documents,
    executors,
    image_executor,
)
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def time(n, d=1):
    return {"numerator": n, "denominator": d}


def example():
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(stream, format="PNG")
    raw = stream.getvalue()
    source, child, root = [str(uuid.uuid4()) for _ in range(3)]

    def ref(id):
        return {"id": id, "revision": 1}

    def layer(start, end, offset=0):
        return {
            "id": str(uuid.uuid4()),
            "name": "layer",
            "source": "source",
            "active": {"start": time(start, 2), "end": time(end, 2)},
            "source_start": time(offset, 2),
        }

    def node(id, kind, payload, inputs=None):
        inputs = inputs or {}
        return {
            "id": id,
            "revision": 1,
            "schema_version": 1,
            "kind": kind,
            "payload": payload,
            "inputs": inputs,
            "content_hash": documents.digest(inputs, payload),
        }

    def composition(layers):
        return {
            "canvas": {"width": 16, "height": 16, "alpha": "opaque"},
            "duration": time(2),
            "frame_rate": time(2),
            "layers": layers,
        }

    nodes = [
        node(
            source,
            "resource",
            {"media_type": "image", "blob_hash": hashlib.sha256(raw).hexdigest()},
        ),
        node(
            child,
            "composition.document",
            composition([layer(1, 2)]),
            {"source": {"target": ref(source), "accepts": "resource"}},
        ),
        node(
            root,
            "composition.document",
            composition([layer(1, 3, 1)]),
            {"source": {"target": ref(child), "accepts": "composition.document"}},
        ),
    ]
    body = {"executor": comp.EXECUTOR, "root": ref(root), "nodes": nodes}
    return {**body, "fingerprint": documents.digest({}, body)}, raw


def test_nested_time_and_half_open_boundaries():
    plan, raw = example()
    comp.preflight(plan["nodes"], plan["root"])
    colors = []
    for at in [Fraction(0), Fraction(1, 2), Fraction(1), Fraction(3, 2)]:
        frame = image_executor.render(comp.frame_plan(plan, at), lambda _: raw)
        colors.append(Image.open(io.BytesIO(frame)).getpixel((8, 8))[:3])
    assert colors == [(0, 0, 0), (255, 0, 0), (0, 0, 0), (0, 0, 0)]


def test_real_mp4_and_unsupported_alpha():
    plan, raw = example()
    with executors.execute(plan, lambda _: raw, None, lambda: False) as (output, kind):
        assert kind == "video"
        assert output.stat().st_size > 500
        assert b"ftyp" in output.read_bytes()[:20]
    assert not output.exists()
    plan["nodes"][-1]["payload"]["canvas"]["alpha"] = "straight"
    with pytest.raises(CapabilityUnsupported):
        comp.preflight(plan["nodes"], plan["root"])


def test_persistent_composition_preview(studio):  # noqa: F811
    from app.models.storage import Blob
    from tests.test_engineering_review import preview, ready

    client, sessions, _, path, _, _ = ready(studio)
    plan, _ = example()
    payload = plan["nodes"][-1]["payload"]
    payload["layers"] = []
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": str(uuid.uuid4()),
                    "expected_revision": 0,
                    "kind": "composition.document",
                    "title": "合成工程",
                    "inputs": {},
                    "payload": payload,
                }
            ],
        },
    )
    rendered = preview(client, path, change)
    assert rendered["preview"]["status"] == "succeeded", rendered
    assert rendered["preview"]["output_type"] == "video"
    with sessions() as db:
        assert db.get(Blob, rendered["preview"]["blob_hash"]).mime == "video/mp4"
    assert client.get(path).json()["revision"] == 0
