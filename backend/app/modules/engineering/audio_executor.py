"""Sample-aligned native audio editing on disk through the shared FFmpeg runner."""

import hashlib
import tempfile
import wave
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import documents
from app.modules.engineering.native import AudioDocument

EXECUTOR = "studio.audio.ffmpeg@1"


def samples(time, rate):
    count, remainder = divmod(time.numerator * rate, time.denominator)
    if remainder:
        raise CapabilityUnsupported("音频时间需要对齐输出采样网格")
    return count


def preflight(nodes, root):
    mapped = {(n["id"], n["revision"]): n for n in nodes}
    if mapped[(root["id"], root["revision"])]["kind"] != "audio.document":
        raise CapabilityUnsupported("音频执行器要求音频文档根")
    active, costs = set(), {}

    def visit(ref):
        key = (ref["id"], ref["revision"])
        if key in active:
            raise Conflict("音频依赖循环")
        if key in costs:
            return costs[key]
        node = mapped[key]
        if node["kind"] == "resource":
            if node["payload"]["media_type"] not in {"audio", "video"}:
                raise CapabilityUnsupported("音频文档仅能使用音频流")
            return 1
        if node["kind"] != "audio.document" or node["schema_version"] != 1:
            raise CapabilityUnsupported("此音频依赖类型或版本未接入执行")
        active.add(key)
        doc = AudioDocument.model_validate(node["payload"])
        total, count = 0, 0
        for track in doc.tracks:
            for clip in track.clips:
                count += 1
                start = samples(clip.start, doc.sample_rate)
                left, right = (
                    samples(clip.source_range.start, doc.sample_rate),
                    samples(clip.source_range.end, doc.sample_rate),
                )
                if start + right - left > 1800 * doc.sample_rate:
                    raise CapabilityUnsupported("当前音频预览最长 30 分钟")
                total += visit(node["inputs"][clip.source]["target"])
        if not count or count > 64 or total > 256:
            raise CapabilityUnsupported("当前音频预览需要 1–64 个片段，嵌套求值最多 256 次")
        active.remove(key)
        costs[key] = total
        return total

    visit(root)


def compile_plan(db, work_id, ref):
    nodes = documents.closure(db, work_id, ref)
    preflight(nodes, ref)
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
    body = {"executor": EXECUTOR, "root": ref, "nodes": nodes}
    return {**body, "fingerprint": documents.digest({}, body)}


@contextmanager
def execute(plan, read_blob, disk_blob, canceled):
    from app.modules.generation import media_engine

    body = {k: v for k, v in plan.items() if k != "fingerprint"}
    if plan["executor"] != EXECUTOR or documents.digest({}, body) != plan["fingerprint"]:
        raise Conflict("音频执行计划校验失败")
    preflight(plan["nodes"], plan["root"])
    nodes = {(n["id"], n["revision"]): n for n in plan["nodes"]}
    cache = {}
    with tempfile.TemporaryDirectory(prefix="inspiration-audio-engineering-") as directory:
        folder = Path(directory)
        counter = 0

        def allocate():
            nonlocal counter
            counter += 1
            return folder / f"part-{counter}.wav"

        def render(ref):
            if canceled():
                raise media_engine.Canceled()
            key = (ref["id"], ref["revision"])
            if key in cache:
                return cache[key]
            node = nodes[key]
            if documents.digest(node["inputs"], node["payload"]) != node["content_hash"]:
                raise Conflict("音频文档校验失败")
            if node["kind"] == "resource":
                source = disk_blob(node["payload"]["blob_hash"])
                with source.open("rb") as stream:
                    checksum = hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        if canceled():
                            raise media_engine.Canceled()
                        checksum.update(chunk)
                    if checksum.hexdigest() != node["payload"]["blob_hash"]:
                        raise Conflict("源声音与固定内容校验不一致")
                cache[key] = source
                return source
            doc = AudioDocument.model_validate(node["payload"])
            clips, duration = [], 0
            for track in doc.tracks:
                for clip in track.clips:
                    source = render(node["inputs"][clip.source]["target"])
                    start, end = (
                        samples(clip.source_range.start, doc.sample_rate),
                        samples(clip.source_range.end, doc.sample_rate),
                    )
                    offset = samples(clip.start, doc.sample_rate)
                    decoded = allocate()
                    media_engine.run(
                        [
                            "-i",
                            str(source),
                            "-map",
                            "0:a:0",
                            "-vn",
                            "-af",
                            f"aresample={doc.sample_rate},atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS",
                            "-ar",
                            str(doc.sample_rate),
                            "-ac",
                            str(doc.channels),
                            "-c:a",
                            "pcm_s16le",
                            str(decoded),
                        ],
                        canceled,
                        timeout=300,
                    )
                    with wave.open(str(decoded), "rb") as audio:
                        if audio.getnframes() != end - start:
                            raise CapabilityUnsupported("片段范围超出源音频，不能静默补齐或截短")
                    gain = (
                        0
                        if track.muted or clip.muted
                        else 10 ** ((track.gain_db + clip.gain_db) / 20)
                    )
                    clips.append((decoded, offset, gain))
                    duration = max(duration, offset + end - start)
            args, filters = [], []
            for index, (file, offset, gain) in enumerate(clips):
                args.extend(["-i", str(file)])
                filters.append(
                    f"[{index}:a:0]volume={gain},adelay=delays={offset}S:all=1[a{index}]"
                )
            labels = "".join(f"[a{i}]" for i in range(len(clips)))
            filters.append(
                f"{labels}amix=inputs={len(clips)}:normalize=0:dropout_transition=0,apad=whole_len={duration},atrim=end_sample={duration}[out]"
            )
            result = allocate()
            media_engine.run(
                [
                    *args,
                    "-filter_complex",
                    ";".join(filters),
                    "-map",
                    "[out]",
                    "-ar",
                    str(doc.sample_rate),
                    "-ac",
                    str(doc.channels),
                    "-c:a",
                    "pcm_s16le",
                    str(result),
                ],
                canceled,
                timeout=300,
            )
            cache[key] = result
            return result

        yield render(plan["root"])
