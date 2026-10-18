"""Bounded, deterministic native image preview, independent of agent runtimes.

Version 1 executes sRGB source-over, placement, opacity and luminance masks.
Unsupported transforms/blends/color spaces fail preflight rather than disappearing.
"""

import hashlib
import io
import uuid

from PIL import Image, ImageChops, ImageOps

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import documents
from app.modules.engineering.native import ImageDocument

EXECUTOR = "studio.image.cpu@1"
MAX_PIXELS = 4096 * 4096


def preflight(nodes, root):
    by_ref = {(n["id"], n["revision"]): n for n in nodes}
    if by_ref[(root["id"], root["revision"])]["kind"] != "image.document":
        raise CapabilityUnsupported("该执行器只处理图像文档")
    for n in nodes:
        if n["kind"] == "resource":
            if n["payload"]["media_type"] != "image":
                raise CapabilityUnsupported("图像执行器不支持此媒体流")
            continue
        if n["kind"] != "image.document" or n["schema_version"] != 1:
            raise CapabilityUnsupported("此依赖尚未接入图像执行器")
        doc = ImageDocument.model_validate(n["payload"])
        if doc.canvas.width * doc.canvas.height > MAX_PIXELS or len(doc.layers) > 64:
            raise CapabilityUnsupported("当前 CPU 预览最多支持 1600 万像素和 64 层")
        if doc.canvas.color_space != "srgb":
            raise CapabilityUnsupported("当前图像执行器只支持 sRGB")
        for layer in doc.layers:
            t = layer.transform
            if layer.blend != "normal" or t.rotation != 0 or t.scale_x != 1 or t.scale_y != 1:
                raise CapabilityUnsupported("当前图像执行器尚未支持该混合模式、旋转或缩放")
            if t.x != int(t.x) or t.y != int(t.y):
                raise CapabilityUnsupported("当前图像执行器只支持整数像素位置")
    if len(nodes) > 128:
        raise CapabilityUnsupported("当前图像执行器依赖超过 128 个节点")
    costs, visiting = {}, set()

    def cost(ref):
        key = (ref["id"], ref["revision"])
        if key in visiting:
            raise Conflict("图像依赖循环")
        if key in costs:
            return costs[key]
        visiting.add(key)
        node = by_ref[key]
        total = 1
        for layer in node["payload"].get("layers", []):
            for name in (layer["source"], layer.get("mask")):
                if name:
                    total += cost(node["inputs"][name]["target"])
        if total > 256:
            raise CapabilityUnsupported("嵌套图像求值次数超过当前预览限制")
        visiting.remove(key)
        costs[key] = total
        return total

    cost(root)


def compile_plan(db, work_id, ref):
    nodes = documents.closure(db, work_id, ref)
    preflight(nodes, ref)
    frozen = [
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
    plan = {"executor": EXECUTOR, "root": ref, "nodes": frozen}
    return {**plan, "fingerprint": documents.digest({}, plan)}


def render(plan, read_blob, canceled=lambda: False):
    from app.modules.generation.media_engine import Canceled

    body = {key: value for key, value in plan.items() if key != "fingerprint"}
    if plan["executor"] != EXECUTOR or documents.digest({}, body) != plan["fingerprint"]:
        raise Conflict("图像执行计划版本或校验和不匹配")
    preflight(plan["nodes"], plan["root"])
    nodes = {(n["id"], n["revision"]): n for n in plan["nodes"]}
    active = set()

    def evaluate(ref):
        if canceled():
            raise Canceled()
        key = (str(uuid.UUID(ref["id"])), ref["revision"])
        if key in active or len(active) > 32:
            raise Conflict("图像依赖循环或过深")
        active.add(key)
        n = nodes[key]
        if documents.digest(n["inputs"], n["payload"]) != n["content_hash"]:
            raise Conflict("图像文档校验失败")
        if n["kind"] == "resource":
            raw = read_blob(n["payload"]["blob_hash"])
            if hashlib.sha256(raw).hexdigest() != n["payload"]["blob_hash"]:
                raise Conflict("源图片内容与固定版本不一致")
            with Image.open(io.BytesIO(raw)) as image:
                if image.mode == "CMYK" or image.info.get("icc_profile"):
                    raise CapabilityUnsupported(
                        "当前图像执行器暂不支持 CMYK 或嵌入式 ICC 转换，请先转为 sRGB"
                    )
                if image.width * image.height > MAX_PIXELS:
                    raise CapabilityUnsupported("源图片超过当前预览像素限制")
                result = ImageOps.exif_transpose(image).convert("RGBA")
        else:
            doc = ImageDocument.model_validate(n["payload"])
            result = Image.new("RGBA", (doc.canvas.width, doc.canvas.height), (0, 0, 0, 0))
            for layer in doc.layers:
                if not layer.enabled:
                    continue
                source = evaluate(n["inputs"][layer.source]["target"])
                alpha = source.getchannel("A")
                if layer.mask:
                    mask = evaluate(n["inputs"][layer.mask]["target"])
                    if mask.size != source.size:
                        raise CapabilityUnsupported("蒙版尺寸必须与源图像一致")
                    alpha = ImageChops.multiply(
                        alpha, ImageChops.multiply(mask.convert("L"), mask.getchannel("A"))
                    )
                alpha = alpha.point(
                    [round(value * layer.transform.opacity) for value in range(256)]
                )
                source.putalpha(alpha)
                result.alpha_composite(source, (int(layer.transform.x), int(layer.transform.y)))
            if doc.canvas.alpha == "opaque":
                background = Image.new("RGBA", result.size, (0, 0, 0, 255))
                result = Image.alpha_composite(background, result)
        active.remove(key)
        return result

    image = evaluate(plan["root"])
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
