"""Trusted document type registry, independent of agents and provider SDKs.

Register versioned schema validation and output meaning independently of executor
implementations. Unknown formats remain opaque rather than flattened. The legacy
millisecond timeline and native rational-time documents retain separate adapters.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.timeline.schemas import TimelineDocumentIn


@dataclass(frozen=True)
class DocumentType:
    kind: str
    schema_version: int
    validate: Callable[[dict, dict], None]
    renderable: bool
    editable: bool = False
    output_streams: tuple[str, ...] = ()


_TYPES: dict[tuple[str, int], DocumentType] = {}
CURRENT: dict[str, int] = {}
_MIGRATIONS: dict[tuple[str, int, int], Callable] = {}


def register(adapter: DocumentType):
    key = (adapter.kind, adapter.schema_version)
    if key in _TYPES or adapter.schema_version < 1:
        raise ValueError("Document type/version already registered or invalid")
    for (kind, _), existing in _TYPES.items():
        if kind == adapter.kind and existing.output_streams != adapter.output_streams:
            raise ValueError("Output meaning cannot change across schema versions; use a new kind")
    _TYPES[key] = adapter
    CURRENT[adapter.kind] = max(CURRENT.get(adapter.kind, 0), adapter.schema_version)


def describe():
    return {
        name: {"schema_version": a.schema_version, "renderable": a.renderable}
        for name, version in CURRENT.items()
        for a in [_TYPES[(name, version)]]
    }


def adapter(kind, version):
    found = _TYPES.get((kind, version))
    if not found:
        raise CapabilityUnsupported("没有注册此文档类型或版本的执行适配器")
    return found


def available(kind, version):
    return (kind, version) in _TYPES


def validate_stored(kind, version, inputs, payload):
    """Unknown extension bodies are preserved, never interpreted or executed.

    References must remain in the shared input envelope; core media/manifests
    cannot bypass their validators by declaring an unknown schema version.
    """
    if available(kind, version):
        return adapter(kind, version).validate(inputs, payload)
    if kind in {"resource", "work.manifest", "sequence.timeline"}:
        raise CapabilityUnsupported("核心工程格式版本尚不支持")
    from app.modules.engineering.native import Input

    if not isinstance(payload, dict) or not isinstance(inputs, dict):
        raise Conflict("扩展文档必须使用对象内容和固定输入")
    for link in inputs.values():
        Input.model_validate(link)


def contracts():
    return [
        {
            "kind": a.kind,
            "schema_version": a.schema_version,
            "editable": a.editable,
            "renderable": a.renderable,
            "output_streams": list(a.output_streams),
        }
        for _, a in sorted(_TYPES.items())
    ]


def register_migration(kind, source, target, transform):
    adapter(kind, source)
    adapter(kind, target)
    key = (kind, source, target)
    if target != source + 1 or key in _MIGRATIONS:
        raise ValueError("Migrations must be unique adjacent forward steps")
    _MIGRATIONS[key] = transform


def migrate(kind, source, target, inputs, payload):
    if target <= source:
        raise CapabilityUnsupported("格式迁移必须指向更新版本；恢复内容请使用历史修订")
    adapter(kind, source).validate(inputs, payload)
    inputs, payload = copy.deepcopy(inputs), copy.deepcopy(payload)
    steps = []
    for version in range(source, target):
        transform = _MIGRATIONS.get((kind, version, version + 1))
        if not transform:
            raise CapabilityUnsupported("缺少经过注册的格式迁移步骤")
        inputs, payload = transform(inputs, payload)
        adapter(kind, version + 1).validate(inputs, payload)
        steps.append({"from": version, "to": version + 1})
    return inputs, payload, steps


def resource(inputs, payload):
    if inputs or set(payload) != {
        "source_kind",
        "source_id",
        "resource_id",
        "blob_hash",
        "media_type",
    }:
        raise Conflict("资源文档字段或依赖不合法")
    if payload["source_kind"] not in {"generation", "library"} or payload["media_type"] not in {
        "image",
        "video",
        "audio",
    }:
        raise CapabilityUnsupported("不支持该媒体资源类型")
    if not payload["blob_hash"]:
        raise Conflict("资源必须固定到文件内容")


def sequence(inputs, payload):
    if set(payload) != {"editor", "render_snapshot", "aspect_ratio"}:
        raise Conflict("剪辑文档包含未支持的字段")
    if payload["aspect_ratio"] not in {"16:9", "9:16", "1:1"}:
        raise CapabilityUnsupported("不支持该画幅")
    if set(payload["editor"]) != {"items", "audio", "subtitles", "visuals"}:
        raise CapabilityUnsupported("旧剪辑适配器不能解释该编辑文档")
    TimelineDocumentIn.model_validate({**payload["editor"], "revision": 0})
    for link in inputs.values():
        if set(link) != {"target", "media_type"} or set(link["target"]) != {"id", "revision"}:
            raise Conflict("必须使用固定文档修订引用")
        if type(link["target"]["revision"]) is not int or link["target"]["revision"] < 1:
            raise Conflict("文档输入不能引用 latest 或无效修订")


register(DocumentType("resource", 1, resource, False))
register(DocumentType("sequence.timeline", 1, sequence, True, output_streams=("picture", "sound")))


def manifest(inputs, payload):
    if set(payload) != {"format", "outputs"} or payload["format"] != 1:
        raise CapabilityUnsupported("不支持该工程清单格式")
    for link in inputs.values():
        if set(link) != {"target", "accepts"} or not isinstance(link["accepts"], str):
            raise CapabilityUnsupported("工程清单包含未支持的输入类型")
        if (
            set(link["target"]) != {"id", "revision"}
            or type(link["target"]["revision"]) is not int
            or link["target"]["revision"] < 1
        ):
            raise Conflict("工程清单必须引用固定文档修订")
    for output in payload["outputs"].values():
        if output.get("input") not in inputs or inputs[output["input"]]["accepts"] == "resource":
            raise Conflict("作品输出没有对应的剪辑根")
        kind = inputs[output["input"]]["accepts"]
        expected = list(adapter(kind, CURRENT[kind]).output_streams) if kind in CURRENT else None
        streams = output.get("streams")
        if not isinstance(streams, list) or any(s not in {"picture", "sound"} for s in streams):
            raise CapabilityUnsupported("输出流声明不合法")
        if expected is not None and streams != expected:
            raise CapabilityUnsupported("输出流与文档类型不匹配")


register(DocumentType("work.manifest", 1, manifest, False))


def register_native_types():
    from functools import partial

    from app.modules.engineering.native import MODELS, validate

    for kind in MODELS:
        streams = ("sound",) if kind == "audio.document" else ("picture",)
        register(DocumentType(kind, 1, partial(validate, kind), False, True, streams))


register_native_types()


def register_timed_types():
    from functools import partial

    from app.modules.engineering import native_v2

    for kind in native_v2.MODELS:
        streams = ("sound",) if kind == "audio.document" else ("picture",)
        register(DocumentType(kind, 2, partial(native_v2.validate, kind), False, True, streams))
        register_migration(kind, 1, 2, partial(native_v2.migrate, kind))


register_timed_types()


def register_sync_type():
    from app.modules.engineering import work_sync

    register(DocumentType(work_sync.KIND, 1, work_sync.validate, False, True, ()))


register_sync_type()
