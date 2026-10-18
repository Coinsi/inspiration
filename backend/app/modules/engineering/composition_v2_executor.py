"""Versioned time-map execution for nested image compositions.

This executor does not pretend to support video decoding, animation curves or
advanced effects. Those remain explicit preflight failures, not dropped layers.
"""

import copy
import tempfile
import uuid
from contextlib import contextmanager
from fractions import Fraction
from math import ceil
from pathlib import Path

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import composition_executor, documents, image_executor, native_v2
from app.modules.engineering.native_v2 import CompositionDocument
from app.modules.engineering.timing import source_at, value

EXECUTOR = "studio.composition.ffmpeg@2"


def seconds(value):
    return Fraction(value.numerator, value.denominator)


def read_document(node):
    if node["schema_version"] == 1:
        _, payload = native_v2.migrate(node["kind"], node["inputs"], node["payload"])
    elif node["schema_version"] == 2:
        payload = node["payload"]
    else:
        raise CapabilityUnsupported("合成时间格式尚未接入此执行器")
    return CompositionDocument.model_validate(payload)


def preflight(nodes, root):
    by_ref = {(n["id"], n["revision"]): n for n in nodes}
    main = by_ref[(root["id"], root["revision"])]
    if main["kind"] != "composition.document" or main["schema_version"] != 2:
        raise CapabilityUnsupported("新版执行器要求第二版合成文档根")
    root_doc = read_document(main)
    # Reuse v1's bounded MP4 encoding envelope, without discarding any actual layer.
    envelope = copy.deepcopy(main)
    envelope["schema_version"] = 1
    envelope["payload"] = root_doc.model_dump(mode="json", exclude={"sync", "layers"})
    envelope["payload"]["layers"] = []
    _, frames = composition_executor.preflight([envelope], root)
    static_nodes = []
    for node in nodes:
        out = copy.deepcopy(node)
        if node["kind"] == "composition.document":
            doc = read_document(node)
            layers = []
            for layer in doc.layers:
                child_ref = node["inputs"][layer.source]["target"]
                child = by_ref[(child_ref["id"], child_ref["revision"])]
                if layer.source_map.audio_policy != "none":
                    raise CapabilityUnsupported("此合成执行器只输出画面，不能忽略声音策略")
                if child["kind"] == "composition.document":
                    source = read_document(child)
                    if value(layer.source_map.source_domain.end) > value(source.duration):
                        raise CapabilityUnsupported("时间映射源域超出源合成时长")
                layers.append(layer.model_dump(mode="json", exclude={"active", "source_map"}))
            out["kind"], out["schema_version"] = "image.document", 1
            out["payload"] = {"canvas": doc.canvas.model_dump(), "layers": layers}
        static_nodes.append(out)
    image_executor.preflight(static_nodes, root)
    return root_doc, frames


def sample_time(doc, coordinate, reverse=False):
    """Pinned frame sampling: forward/hold uses floor; reverse uses left limit.

    A reverse interval beginning at the source end selects its last frame, never
    an empty frame outside the half-open domain. No floating epsilon is used.
    """
    fps = value(doc.frame_rate)
    index = ceil(coordinate * fps) - 1 if reverse else (coordinate * fps).__floor__()
    at = Fraction(index) / fps
    if not 0 <= at < value(doc.duration):
        raise CapabilityUnsupported("映射位置没有可采样的源合成帧")
    return at


def compile_plan(db, work_id, root):
    nodes = documents.closure(db, work_id, root)
    preflight(nodes, root)
    nodes = [
        {
            k: n[k]
            for k in (
                "id",
                "revision",
                "kind",
                "schema_version",
                "content_hash",
                "inputs",
                "payload",
            )
        }
        for n in nodes
    ]
    body = {"executor": EXECUTOR, "root": root, "nodes": nodes}
    return {**body, "fingerprint": documents.digest({}, body)}


def frame_plan(plan, time):
    by_ref = {(n["id"], n["revision"]): n for n in plan["nodes"]}
    frozen = {}

    def freeze(ref, at, reverse=False):
        node = by_ref[(ref["id"], ref["revision"])]
        converted = copy.deepcopy(node)
        if node["kind"] == "composition.document":
            doc = read_document(node)
            at = sample_time(doc, at, reverse)
            converted["id"] = str(uuid.uuid5(uuid.UUID(node["id"]), f"frame:{at}"))
            converted["kind"] = "image.document"
            converted["schema_version"] = 1
            converted["inputs"] = {}
            layers = []
            for layer in doc.layers:
                if not layer.enabled or not seconds(layer.active.start) <= at < seconds(
                    layer.active.end
                ):
                    continue
                item = layer.model_dump(mode="json", exclude={"active", "source_map"})
                name = str(layer.id) + ":source"
                link = copy.deepcopy(node["inputs"][layer.source])
                local = at - value(layer.active.start)
                segment = next(
                    s
                    for s in layer.source_map.segments
                    if value(s.local.start) <= local < value(s.local.end)
                )
                link["target"] = freeze(
                    link["target"], source_at(layer.source_map, local), value(segment.rate) < 0
                )
                link["accepts"] = (
                    "resource"
                    if by_ref[
                        (
                            node["inputs"][layer.source]["target"]["id"],
                            node["inputs"][layer.source]["target"]["revision"],
                        )
                    ]["kind"]
                    == "resource"
                    else "image.document"
                )
                converted["inputs"][name] = link
                item["source"] = name
                if layer.mask:
                    mask = copy.deepcopy(node["inputs"][layer.mask])
                    mask["target"] = freeze(mask["target"], Fraction(0))
                    mask_name = str(layer.id) + ":mask"
                    converted["inputs"][mask_name] = mask
                    item["mask"] = mask_name
                layers.append(item)
            converted["payload"] = {"canvas": doc.canvas.model_dump(), "layers": layers}
        else:
            for link in converted["inputs"].values():
                link["target"] = freeze(link["target"], Fraction(0))
        converted["content_hash"] = documents.digest(converted["inputs"], converted["payload"])
        fixed = {"id": converted["id"], "revision": converted["revision"]}
        frozen[(fixed["id"], fixed["revision"])] = converted
        return fixed

    root = freeze(plan["root"], time)
    body = {"executor": image_executor.EXECUTOR, "root": root, "nodes": list(frozen.values())}
    return {**body, "fingerprint": documents.digest({}, body)}


@contextmanager
def execute(plan, read_blob, disk_blob, canceled):
    from app.modules.generation import media_engine

    body = {k: v for k, v in plan.items() if k != "fingerprint"}
    if plan["executor"] != EXECUTOR or documents.digest({}, body) != plan["fingerprint"]:
        raise Conflict("合成执行计划校验失败")
    for node in plan["nodes"]:
        if documents.digest(node["inputs"], node["payload"]) != node["content_hash"]:
            raise Conflict("合成依赖校验失败")
    doc, frames = preflight(plan["nodes"], plan["root"])
    fps = seconds(doc.frame_rate)
    with tempfile.TemporaryDirectory(prefix="inspiration-composition-") as directory:
        folder = Path(directory)
        for index in range(frames):
            if canceled():
                raise media_engine.Canceled()
            pixels = image_executor.render(
                frame_plan(plan, Fraction(index) / fps), read_blob, canceled
            )
            (folder / f"frame-{index:06d}.png").write_bytes(pixels)
        output = folder / "preview.mp4"
        media_engine.run(
            [
                "-framerate",
                f"{fps.numerator}/{fps.denominator}",
                "-i",
                str(folder / "frame-%06d.png"),
                "-frames:v",
                str(frames),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                "-movflags",
                "+faststart",
                str(output),
            ],
            canceled,
            timeout=300,
        )
        yield output
