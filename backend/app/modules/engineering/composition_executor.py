"""Timed image compositions compiled at exact rational frame times.

This executor does not pretend to support video decoding, animation curves or
advanced effects. Those remain explicit preflight failures, not dropped layers.
"""

import copy
import tempfile
import uuid
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import documents, image_executor
from app.modules.engineering.native import CompositionDocument

EXECUTOR = "studio.composition.ffmpeg@1"


def seconds(value):
    return Fraction(value.numerator, value.denominator)


def preflight(nodes, root):
    if any(n["kind"] == "composition.document" and n["schema_version"] != 1 for n in nodes):
        raise CapabilityUnsupported("当前合成执行器不支持此时间映射格式，不能忽略后渲染")
    by_ref = {(n["id"], n["revision"]): n for n in nodes}
    main = by_ref[(root["id"], root["revision"])]
    if main["kind"] != "composition.document":
        raise CapabilityUnsupported("动态合成执行器要求合成文档根")
    root_doc = CompositionDocument.model_validate(main["payload"])
    frames = seconds(root_doc.duration) * seconds(root_doc.frame_rate)
    if (
        seconds(root_doc.frame_rate) > 120
        or max(root_doc.canvas.width, root_doc.canvas.height) > 4096
    ):
        raise CapabilityUnsupported("当前合成预览最多 120 fps，单边最多 4096 像素")
    if root_doc.canvas.alpha != "opaque":
        raise CapabilityUnsupported("当前 MP4 输出要求明确选择不透明画布")
    if frames.denominator != 1 or frames < 1 or frames > 1800:
        raise CapabilityUnsupported("当前合成预览需要整数帧时长，最多 1800 帧")
    if root_doc.canvas.width % 2 or root_doc.canvas.height % 2:
        raise CapabilityUnsupported("当前 H.264 预览要求偶数宽高")
    if frames * root_doc.canvas.width * root_doc.canvas.height > 512_000_000:
        raise CapabilityUnsupported("合成预览超过当前帧像素预算，请降低画布尺寸或缩短时长")
    static_nodes = []
    for node in nodes:
        out = copy.deepcopy(node)
        if node["kind"] == "composition.document":
            doc = CompositionDocument.model_validate(node["payload"])
            layers = []
            for layer in doc.layers:
                child_ref = node["inputs"][layer.source]["target"]
                child = by_ref[(child_ref["id"], child_ref["revision"])]
                if child["kind"] == "composition.document":
                    source = CompositionDocument.model_validate(child["payload"])
                    end = (
                        seconds(layer.source_start)
                        + seconds(layer.active.end)
                        - seconds(layer.active.start)
                    )
                    if end > seconds(source.duration):
                        raise CapabilityUnsupported("嵌套合成的使用范围超出源合成")
                elif layer.source_start.numerator:
                    raise CapabilityUnsupported("静态图片的源起点必须为零")
                layers.append(layer.model_dump(mode="json", exclude={"active", "source_start"}))
            out["kind"] = "image.document"
            out["payload"] = {"canvas": doc.canvas.model_dump(), "layers": layers}
        static_nodes.append(out)
    image_executor.preflight(static_nodes, root)
    return root_doc, int(frames)


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

    def freeze(ref, at):
        node = by_ref[(ref["id"], ref["revision"])]
        converted = copy.deepcopy(node)
        if node["kind"] == "composition.document":
            doc = CompositionDocument.model_validate(node["payload"])
            converted["id"] = str(uuid.uuid5(uuid.UUID(node["id"]), f"frame:{at}"))
            converted["kind"] = "image.document"
            converted["inputs"] = {}
            layers = []
            for layer in doc.layers:
                if not layer.enabled or not seconds(layer.active.start) <= at < seconds(
                    layer.active.end
                ):
                    continue
                item = layer.model_dump(mode="json", exclude={"active", "source_start"})
                name = str(layer.id) + ":source"
                link = copy.deepcopy(node["inputs"][layer.source])
                link["target"] = freeze(
                    link["target"], at - seconds(layer.active.start) + seconds(layer.source_start)
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
