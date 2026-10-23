"""Bounded technical acceptance for immutable media, independent of creative QA.

prepare_validation/record_validation are short database phases. The expensive
validate_prepared phase has no database or production-run locks. The convenience
validate_output entry point must therefore be called by a validation worker,
never while holding a ProductionRun lock. Receipts can also live in task.state
and survive process restarts; they are not proof of narrative quality.
"""

import copy
import hashlib
import json
import re
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.errors import Conflict
from app.models.generation import Generation, GenerationJob
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.harness.context import ContextReference, revalidate_context
from app.modules.harness.media_context import describe_media
from app.storage import cas

VALIDATOR_VERSION = "studio.media.technical@1"
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_DURATION_MS = 30 * 60 * 1000
MAX_PIXELS = 40_000_000
MAX_DECODE_PIXELS = 80_000_000_000
MAX_SECONDS = 300
CACHE_KEY = "harness_technical_validation"
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_VALIDATION_LOCKS = [threading.Lock() for _ in range(32)]


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    duration_ms: int | None = Field(default=None, gt=0)
    duration_tolerance_ms: int = Field(default=250, ge=0, le=1000)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    has_audio: bool | None = None
    has_video: bool | None = None


@dataclass(frozen=True)
class PreparedValidation:
    reference: dict
    blob_hash: str
    media_type: str
    mime: str
    size_bytes: int
    storage_uri: str = field(repr=False)
    source_fingerprint: str
    expected: dict
    expected_fingerprint: str
    job_id: uuid.UUID | None
    cached_receipt: dict | None = None


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _reference(output):
    if isinstance(output, Generation):
        return ContextReference(
            kind="generation", id=output.id, expected_blob_hash=output.output_blob_hash
        )
    if isinstance(output, ContextReference):
        return output
    if isinstance(output, dict) and "reference" in output:
        return ContextReference.model_validate(output["reference"])
    return ContextReference.model_validate(output)


def _identity(blob):
    identity = {"uri": blob.storage_uri, "size_bytes": blob.size_bytes, "mime": blob.mime}
    path = Path(blob.storage_uri)
    if path.is_absolute() or path.is_file():
        try:
            stat = path.stat()
            identity["physical"] = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
        except OSError:
            identity["physical"] = "missing"
    # CAS repairs allocate a new remote object identity; the URI never appears
    # in user-visible receipts. Remote bytes remain subject to CAS immutability.
    return _digest(identity)


def prepare_validation(db, ctx, output, *, expected=None):
    """Authorize and detach fixed identity/expectations without decoding media."""
    ctx = revalidate_context(db, ctx)
    reference = _reference(output)
    if reference.kind == "document" or reference.start_ms is not None or reference.region:
        raise ValueError("技术交付验收要求完整固定媒体，不能用区域或片段检查代替整份结果")
    info = describe_media(db, ctx, reference)
    reference = info["reference"]
    blob = db.get(Blob, reference["expected_blob_hash"], populate_existing=True)
    metadata = info["metadata"]
    required = {
        k: metadata[k]
        for k in ("duration_ms", "width", "height", "has_audio")
        if metadata.get(k) is not None
    }
    if info["media_type"] == "video":
        required["has_video"] = True
    elif info["media_type"] == "audio":
        required["has_audio"] = True
    required.update(Expectations.model_validate(expected or {}).model_dump(exclude_unset=True))
    required = Expectations.model_validate(required).model_dump()
    job = None
    if reference["kind"] == "generation":
        generation = db.get(Generation, uuid.UUID(reference["id"]))
        job = db.get(GenerationJob, generation.job_id, populate_existing=True)
    prepared = PreparedValidation(
        reference=reference,
        blob_hash=blob.hash,
        media_type=info["media_type"],
        mime=blob.mime,
        size_bytes=blob.size_bytes,
        storage_uri=blob.storage_uri,
        source_fingerprint=_identity(blob),
        expected=required,
        expected_fingerprint=_digest(required),
        job_id=job.id if job else None,
    )
    stored = (job.cost_raw or {}).get(CACHE_KEY, {}).get(blob.hash) if job else None
    if stored and receipt_matches(prepared, stored):
        return PreparedValidation(**{**prepared.__dict__, "cached_receipt": copy.deepcopy(stored)})
    return prepared


def receipt_matches(prepared, receipt):
    """Only terminal full verification evidence is reusable, never timeouts."""
    return bool(
        isinstance(receipt, dict)
        and receipt.get("validator_version") == VALIDATOR_VERSION
        and receipt.get("blob_hash") == prepared.blob_hash
        and receipt.get("source_fingerprint") == prepared.source_fingerprint
        and receipt.get("expected_fingerprint") == prepared.expected_fingerprint
        and receipt.get("media_type") == prepared.media_type
        and receipt.get("status") in {"passed", "failed"}
        and (
            receipt.get("status") == "failed"
            or (
                receipt.get("technical_ready") is True
                and receipt.get("coverage", {}).get("full_decode") is True
                and receipt.get("coverage", {}).get("hash_bytes") == prepared.size_bytes
            )
        )
    )


def _cache_key(prepared):
    return (
        VALIDATOR_VERSION,
        prepared.blob_hash,
        prepared.media_type,
        prepared.source_fingerprint,
        prepared.expected_fingerprint,
    )


def _cached(prepared, prior_receipt=None):
    for receipt in (prior_receipt, prepared.cached_receipt):
        if receipt_matches(prepared, receipt):
            return {**copy.deepcopy(receipt), "cache_hit": True}
    with _CACHE_LOCK:
        key = _cache_key(prepared)
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return {**copy.deepcopy(_CACHE[key]), "cache_hit": True}
    return None


def cached_validation(db, ctx, output, *, expected=None, prior_receipt=None):
    """Fast cache lookup; authorization and physical identity are checked again."""
    return _cached(prepare_validation(db, ctx, output, expected=expected), prior_receipt)


def _receipt(prepared):
    return {
        "validator_version": VALIDATOR_VERSION,
        "blob_hash": prepared.blob_hash,
        "source_fingerprint": prepared.source_fingerprint,
        "expected_fingerprint": prepared.expected_fingerprint,
        "media_type": prepared.media_type,
        "status": "not_checked",
        "technical_ready": False,
        "cache_hit": False,
        "checked_at": datetime.now(UTC).isoformat(),
        "expected": prepared.expected,
        "checks": [],
        "metadata": {},
        "coverage": {
            "hash_bytes": 0,
            "full_decode": False,
            "decoded_until_ms": 0,
            "streams_checked": [],
            "frames_decoded": 0,
        },
        "unassessed": ["creative_quality", "plot_consistency", "instruction_fulfillment"],
    }


def _check(result, name, passed, *, actual=None, expected=None, reason=None):
    result["checks"].append(
        {
            "name": name,
            "status": "passed" if passed else "failed",
            "actual": actual,
            "expected": expected,
            "reason": reason,
        }
    )
    return passed


def _stop(result, status, code, message):
    result.update(status=status, technical_ready=False, error={"code": code, "message": message})
    return result


def _expect(result, prepared, metadata):
    for key in ("width", "height", "has_audio", "has_video", "duration_ms"):
        expected = prepared.expected.get(key)
        if expected is None:
            continue
        actual = metadata.get(key)
        passed = actual == expected
        if key == "duration_ms" and actual is not None:
            passed = abs(actual - expected) <= prepared.expected["duration_tolerance_ms"]
        if not _check(result, "expected_" + key, passed, actual=actual, expected=expected):
            return False
    return True


def _probe(path, canceled, timeout):
    log = media_engine.run(
        [
            "-threads",
            "2",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-t",
            "0.05",
            "-f",
            "null",
            "-",
        ],
        canceled,
        timeout=timeout,
    ).split("Output #", 1)[0]
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", log)
    dimensions = re.search(r"Video: [^\n]*?\b(\d{1,5})x(\d{1,5})\b", log)
    fps = re.search(r"Video: [^\n]*?\b(\d+(?:\.\d+)?) fps", log)
    metadata = {
        "has_audio": "Audio:" in log,
        "has_video": "Video:" in log,
        "audio_stream_count": len(re.findall(r"Stream #\d+:\d+[^\n]*Audio:", log)),
        "video_stream_count": len(re.findall(r"Stream #\d+:\d+[^\n]*Video:", log)),
    }
    if duration:
        h, m, s = map(float, duration.groups())
        metadata["duration_ms"] = round((h * 3600 + m * 60 + s) * 1000)
    if dimensions:
        metadata.update(width=int(dimensions[1]), height=int(dimensions[2]))
    if fps:
        metadata["frame_rate"] = float(fps[1])
    return metadata


def _image(path, prepared, result):
    with Image.open(path) as source:
        width, height = source.size
        if width * height > MAX_PIXELS:
            return _stop(result, "not_checked", "PIXEL_LIMIT", "图片超出技术验收像素预算")
        if getattr(source, "n_frames", 1) != 1:
            return _stop(result, "unsupported", "ANIMATED_IMAGE", "动画图片尚未接入整份交付验收")
        source.verify()
    with Image.open(path) as source:
        source.load()
    result["metadata"] = {"width": width, "height": height, "has_video": False, "has_audio": False}
    result["coverage"].update(full_decode=True, frames_decoded=1, streams_checked=["image"])
    _check(result, "image_decode", True)
    if not _expect(result, prepared, result["metadata"]):
        return _stop(result, "failed", "EXPECTATION_MISMATCH", "图片尺寸与固定输出要求不一致")
    result.update(status="passed", technical_ready=True)
    return result


def _av(path, prepared, result, canceled, started):
    def remaining():
        return max(0.1, MAX_SECONDS - (time.monotonic() - started))

    metadata = _probe(path, canceled, min(30, remaining()))
    result["metadata"] = metadata
    if metadata["video_stream_count"] > 1 or metadata["audio_stream_count"] > 8:
        return _stop(result, "unsupported", "MULTI_STREAM_LIMIT", "此多流媒体尚未接入完整交付验收")
    required = "has_video" if prepared.media_type == "video" else "has_audio"
    if not _check(
        result, "required_stream", metadata[required], actual=metadata, expected=required
    ):
        return _stop(result, "failed", "MISSING_STREAM", "结果缺少所声明的画面或音频流")
    duration = metadata.get("duration_ms")
    if not duration:
        return _stop(
            result, "unsupported", "UNKNOWN_DURATION", "无法确定整份媒体时长，不能声明完整验收"
        )
    if duration > MAX_DURATION_MS:
        return _stop(result, "not_checked", "DURATION_LIMIT", "媒体时长超出本次全片验收上限")
    width, height = metadata.get("width", 0), metadata.get("height", 0)
    pixels = width * height
    if metadata["has_video"] and (not pixels or pixels > MAX_PIXELS):
        return _stop(result, "not_checked", "PIXEL_LIMIT", "视频尺寸超出技术验收预算或无法确认")
    if pixels * (duration / 1000) * metadata.get("frame_rate", 60) > MAX_DECODE_PIXELS:
        return _stop(result, "not_checked", "DECODE_BUDGET", "全片像素帧量超出本次验收预算")
    if not _expect(result, prepared, metadata):
        return _stop(result, "failed", "EXPECTATION_MISMATCH", "时长、媒体流或尺寸与输出要求不一致")
    maps = (
        ["-map", "0:v:0", "-map", "0:a?"] if prepared.media_type == "video" else ["-map", "0:a:0"]
    )
    log = media_engine.run(
        [
            "-xerror",
            "-threads",
            "2",
            "-err_detect",
            "explode",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            *maps,
            "-sn",
            "-dn",
            "-t",
            str(MAX_DURATION_MS / 1000 + 1),
            "-progress",
            "pipe:2",
            "-nostats",
            "-f",
            "null",
            "-",
        ],
        canceled,
        timeout=remaining(),
    )
    times = re.findall(r"^out_time_us=(\d+)\r?$", log, flags=re.MULTILINE)
    frames = re.findall(r"^frame=\s*(\d+)\r?$", log, flags=re.MULTILINE)
    decoded_ms = round(max(map(int, times), default=0) / 1000)
    frame_count = max(map(int, frames), default=0)
    result["metadata"].update(decoded_duration_ms=decoded_ms)
    result["coverage"].update(
        decoded_until_ms=decoded_ms,
        frames_decoded=frame_count,
        streams_checked=[
            s
            for s in ("video", "audio")
            if metadata.get("has_" + s) and (prepared.media_type == "video" or s == "audio")
        ],
    )
    tolerance = max(100, round(2000 / metadata.get("frame_rate", 24)))
    if not _check(
        result,
        "decoded_duration",
        abs(decoded_ms - duration) <= tolerance,
        actual=decoded_ms,
        expected=duration,
    ):
        return _stop(result, "failed", "INCOMPLETE_DECODE", "实际可解码时长与容器时长不一致")
    if prepared.media_type == "video" and not _check(
        result, "video_frames", frame_count > 0, actual=frame_count, expected=">0"
    ):
        return _stop(result, "failed", "NO_DECODED_FRAMES", "视频没有实际可解码画面")
    _check(result, "full_stream_decode", "progress=end" in log)
    if "progress=end" not in log:
        return _stop(result, "not_checked", "DECODE_UNCONFIRMED", "没有收到完整解码结束记录")
    result["coverage"]["full_decode"] = True
    result.update(status="passed", technical_ready=True)
    return result


def _validate(prepared, canceled):
    result = _receipt(prepared)
    if canceled():
        return _stop(result, "not_checked", "CANCELED", "技术验收已取消")
    if prepared.size_bytes <= 0 or prepared.size_bytes > MAX_SOURCE_BYTES:
        return _stop(result, "not_checked", "SOURCE_SIZE_LIMIT", "源文件超出本次技术验收大小预算")
    started = time.monotonic()
    blob = SimpleNamespace(
        hash=prepared.blob_hash,
        storage_uri=prepared.storage_uri,
        size_bytes=prepared.size_bytes,
        mime=prepared.mime,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="inspiration-technical-") as directory:
            # Decode our verified immutable disk copy. Both local and remote CAS
            # reads are bounded, including a damaged object larger than its row.
            path = Path(directory) / "source"
            digest, count = hashlib.sha256(), 0
            with cas.open_range(blob, 0, MAX_SOURCE_BYTES + 1) as source, path.open("wb") as target:
                while chunk := source.read(min(1024 * 1024, MAX_SOURCE_BYTES + 1 - count)):
                    if canceled():
                        raise media_engine.Canceled()
                    if time.monotonic() - started > MAX_SECONDS:
                        return _stop(
                            result, "not_checked", "TIME_LIMIT", "技术验收超时，不能当作成功"
                        )
                    count += len(chunk)
                    result["coverage"]["hash_bytes"] = count
                    if count > MAX_SOURCE_BYTES:
                        return _stop(
                            result, "not_checked", "SOURCE_SIZE_LIMIT", "实际文件超出读取预算"
                        )
                    digest.update(chunk)
                    target.write(chunk)
            if not _check(
                result,
                "content_integrity",
                count == prepared.size_bytes and digest.hexdigest() == prepared.blob_hash,
                actual={"size_bytes": count, "sha256": digest.hexdigest()},
                expected={"size_bytes": prepared.size_bytes, "sha256": prepared.blob_hash},
            ):
                return _stop(
                    result, "failed", "CONTENT_MISMATCH", "实际文件与固定内容哈希或长度不一致"
                )
            if prepared.media_type == "image":
                return _image(path, prepared, result)
            if prepared.media_type in {"audio", "video"}:
                return _av(path, prepared, result, canceled, started)
            return _stop(result, "unsupported", "MEDIA_TYPE", "此媒体类型没有技术验收器")
    except media_engine.Canceled:
        return _stop(result, "not_checked", "CANCELED", "技术验收已取消")
    except FileNotFoundError:
        return _stop(result, "failed", "SOURCE_UNAVAILABLE", "源文件或媒体解码程序不可用")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        message = str(exc)
        if "超时" in message or time.monotonic() - started > MAX_SECONDS:
            return _stop(result, "not_checked", "TIME_LIMIT", "技术验收超时，不能当作成功")
        if any(
            s in message.lower() for s in ("decoder not found", "unknown decoder", "no decoder")
        ):
            return _stop(result, "unsupported", "CODEC_UNAVAILABLE", "当前验收器没有此编码的解码器")
        return _stop(result, "failed", "DECODE_FAILED", "文件损坏、截断或无法完整解码")


def validate_prepared(prepared, *, prior_receipt=None, canceled=lambda: False, force=False):
    """Slow phase: no db argument and no run lock; returns a JSON-safe receipt."""
    if not force:
        cached = _cached(prepared, prior_receipt)
        if cached:
            return cached
    lock = _VALIDATION_LOCKS[int(prepared.blob_hash[:8], 16) % len(_VALIDATION_LOCKS)]
    waiting_since = time.monotonic()
    while not lock.acquire(timeout=0.2):
        if canceled():
            return _stop(_receipt(prepared), "not_checked", "CANCELED", "技术验收等待已取消")
        if time.monotonic() - waiting_since > MAX_SECONDS:
            return _stop(_receipt(prepared), "not_checked", "TIME_LIMIT", "技术验收等待超时")
    try:
        if not force:
            cached = _cached(prepared, prior_receipt)
            if cached:
                return cached
        result = _validate(prepared, canceled)
        if result["status"] in {"passed", "failed"}:
            with _CACHE_LOCK:
                _CACHE[_cache_key(prepared)] = copy.deepcopy(result)
                _CACHE.move_to_end(_cache_key(prepared))
                while len(_CACHE) > 128:
                    _CACHE.popitem(last=False)
        return result
    finally:
        lock.release()


def record_validation(db, ctx, prepared, receipt):
    """Short write phase; caller commits. Never adopts or changes job success."""
    # Revalidate after potentially long IO; a completed check is not retained
    # under access that has since been revoked or a source repaired in place.
    if (
        receipt.get("validator_version") != VALIDATOR_VERSION
        or receipt.get("blob_hash") != prepared.blob_hash
        or receipt.get("source_fingerprint") != prepared.source_fingerprint
        or receipt.get("expected_fingerprint") != prepared.expected_fingerprint
        or receipt.get("media_type") != prepared.media_type
    ):
        raise Conflict("技术验收收据与固定媒体或验收要求不一致")
    current = prepare_validation(db, ctx, prepared.reference, expected=prepared.expected)
    if (
        current.source_fingerprint != prepared.source_fingerprint
        or current.blob_hash != prepared.blob_hash
    ):
        return _stop(
            _receipt(current), "not_checked", "SOURCE_CHANGED", "验收期间源对象已改变，请重新检查"
        )
    if prepared.job_id:
        job = db.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.id == prepared.job_id,
                GenerationJob.project_id == ctx.project.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job:
            raw = copy.deepcopy(job.cost_raw or {})
            records = raw.setdefault(CACHE_KEY, {})
            records[prepared.blob_hash] = copy.deepcopy(receipt)
            # A job can yield several outputs; this cannot grow with every retry.
            job.cost_raw = raw
            db.flush()
    return receipt


def validate_output(
    db, ctx, output, *, expected=None, prior_receipt=None, canceled=lambda: False, force=False
):
    """Worker convenience API. Do not call inside a production-run row lock."""
    prepared = prepare_validation(db, ctx, output, expected=expected)
    receipt = validate_prepared(
        prepared, prior_receipt=prior_receipt, canceled=canceled, force=force
    )
    return record_validation(db, ctx, prepared, receipt)
