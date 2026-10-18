"""Trusted execution plugins shared by API, workers and capability discovery.

Queued plans pin an executor identity. Registering a replacement does not change
old jobs. Plugins own preflight and temporary files; the worker owns job state.
"""

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import documents


@dataclass(frozen=True)
class Executor:
    id: str
    kind: str
    schema_version: int
    output_type: str
    compile: Callable
    execute: Callable
    features: tuple[str, ...]
    max_source_bytes: int
    mime: str = "image/png"


_EXECUTORS: dict[str, Executor] = {}
_DEFAULTS: dict[tuple[str, int], str] = {}


def register(executor: Executor, *, default=False):
    if executor.id in _EXECUTORS:
        raise ValueError("Executor identity is immutable; register a new version")
    if executor.output_type not in {"image", "audio", "video"} or executor.max_source_bytes <= 0:
        raise ValueError("Invalid executor output or source limit")
    _EXECUTORS[executor.id] = executor
    key = (executor.kind, executor.schema_version)
    if default or key not in _DEFAULTS:
        _DEFAULTS[key] = executor.id


def describe():
    return [
        {
            "id": e.id,
            "kind": e.kind,
            "schema_version": e.schema_version,
            "output_type": e.output_type,
            "features": list(e.features),
            "max_source_bytes": e.max_source_bytes,
            "requires_preflight": True,
            "default": _DEFAULTS.get((e.kind, e.schema_version)) == e.id,
        }
        for e in _EXECUTORS.values()
    ]


def resolve(identity):
    executor = _EXECUTORS.get(identity)
    if executor is None:
        raise CapabilityUnsupported("执行器版本不可用，已冻结任务不能自动切换到其他版本")
    return executor


def compile_plan(db, work_id, ref):
    import uuid

    from app.models.storage import Blob

    doc, revision = documents.read_revision(db, work_id, uuid.UUID(ref["id"]), ref["revision"])
    executor = resolve(_DEFAULTS.get((doc.kind, revision.schema_version)))
    plan = executor.compile(db, work_id, ref)
    if plan.get("executor") != executor.id:
        raise Conflict("执行计划未固定当前执行器身份")
    for node in plan["nodes"]:
        if node["kind"] == "resource":
            blob = db.get(Blob, node["payload"]["blob_hash"])
            if not blob or blob.size_bytes > executor.max_source_bytes:
                raise CapabilityUnsupported("源文件超过此执行器上限或已不可用")
    verify(plan)
    return plan, executor.output_type


def verify(plan):
    executor = resolve(plan.get("executor"))
    body = {k: v for k, v in plan.items() if k != "fingerprint"}
    if documents.digest({}, body) != plan.get("fingerprint"):
        raise Conflict("执行计划校验失败")
    return executor


@contextmanager
def execute(plan, read_blob, disk_blob, canceled):
    executor = verify(plan)
    with executor.execute(plan, read_blob, disk_blob, canceled) as result:
        yield result, executor.output_type


@contextmanager
def image_execute(plan, read_blob, disk_blob, canceled):
    from app.modules.engineering.image_executor import render

    yield render(plan, read_blob, canceled)


def _register_builtins():
    from app.modules.engineering import audio_executor, composition_executor, image_executor

    register(
        Executor(
            image_executor.EXECUTOR,
            "image.document",
            1,
            "image",
            image_executor.compile_plan,
            image_execute,
            ("srgb", "source-over", "integer-position", "opacity", "luminance-mask"),
            64 * 1024 * 1024,
        )
    )
    register(
        Executor(
            audio_executor.EXECUTOR,
            "audio.document",
            1,
            "audio",
            audio_executor.compile_plan,
            audio_executor.execute,
            ("sample-aligned-trim", "clip-placement", "gain", "mute", "mix", "nested-audio"),
            10 * 1024 * 1024 * 1024,
            "audio/wav",
        )
    )
    register(
        Executor(
            composition_executor.EXECUTOR,
            "composition.document",
            1,
            "video",
            composition_executor.compile_plan,
            composition_executor.execute,
            ("timed-image-layers", "nested-composition", "rational-frame-time", "opaque-mp4"),
            64 * 1024 * 1024,
            "video/mp4",
        )
    )

    from app.modules.engineering import composition_v2_executor

    register(
        Executor(
            composition_v2_executor.EXECUTOR,
            "composition.document",
            2,
            "video",
            composition_v2_executor.compile_plan,
            composition_v2_executor.execute,
            (
                "timed-image-layers",
                "nested-composition",
                "rational-time-map",
                "repeat",
                "reverse-left-limit",
                "hold",
                "opaque-mp4",
            ),
            64 * 1024 * 1024,
            "video/mp4",
        )
    )

    from app.modules.engineering import audio_v2_executor

    register(
        Executor(
            audio_v2_executor.EXECUTOR,
            "audio.document",
            2,
            "audio",
            audio_v2_executor.compile_plan,
            audio_v2_executor.execute,
            (
                "sample-aligned-time-map",
                "repeat",
                "reverse",
                "resample",
                "pitch-preserving-stretch",
                "muted-hold",
                "nested-audio",
            ),
            10 * 1024 * 1024 * 1024,
            "audio/wav",
        )
    )


_register_builtins()
