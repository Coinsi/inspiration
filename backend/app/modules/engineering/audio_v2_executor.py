"""Versioned piecewise audio mapping with explicit pitch and silence policies.

Source spans are checked before DSP. Final output boundaries are sample exact;
stretching preserves pitch, resampling changes it, reverse reverses samples,
and a hold is only executable as explicitly requested silence.
"""

import hashlib
import tempfile
import wave
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import documents, native_v2
from app.modules.engineering.timing import value

EXECUTOR = "studio.audio.ffmpeg@2"


def read_document(node):
    if node["schema_version"] == 1:
        _, payload = native_v2.migrate(node["kind"], node["inputs"], node["payload"])
    elif node["schema_version"] == 2:
        payload = node["payload"]
    else:
        raise CapabilityUnsupported("音频时间映射格式尚未接入执行器")
    return native_v2.AudioDocument.model_validate(payload)


def sample_count(time, rate):
    count = time * rate
    if count.denominator != 1:
        raise CapabilityUnsupported("音频映射边界需要对齐输出采样网格")
    return int(count)


def preflight(nodes, root):
    mapped = {(n["id"], n["revision"]): n for n in nodes}
    main = mapped[(root["id"], root["revision"])]
    if main["kind"] != "audio.document" or main["schema_version"] != 2:
        raise CapabilityUnsupported("第二版音频执行器要求第二版音频文档根")
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
                raise CapabilityUnsupported("音频文档只能引用音频流")
            return 1
        if node["kind"] != "audio.document":
            raise CapabilityUnsupported("此依赖类型尚未接入音频执行器")
        active.add(key)
        doc = read_document(node)
        total, count = 0, 0
        for track in doc.tracks:
            for clip in track.clips:
                mapping = clip.source_map
                if mapping.audio_policy == "none":
                    raise CapabilityUnsupported("音频必须明确声音处理策略")
                end = sample_count(value(clip.start) + value(mapping.duration), doc.sample_rate)
                if end > 1800 * doc.sample_rate:
                    raise CapabilityUnsupported("音频预览最长 30 分钟")
                sample_count(value(clip.start), doc.sample_rate)
                for segment in mapping.segments:
                    count += 1
                    duration = value(segment.local.end) - value(segment.local.start)
                    speed = abs(value(segment.rate))
                    for boundary in (
                        value(segment.local.start),
                        duration,
                        value(segment.source_start),
                        duration * speed,
                    ):
                        sample_count(boundary, doc.sample_rate)
                    if speed and not 1 / 16 <= speed <= 16:
                        raise CapabilityUnsupported("音频变速范围为 1/16 到 16 倍")
                    if duration * speed > 1800:
                        raise CapabilityUnsupported("单段音频源范围最多 30 分钟")
                    if mapping.audio_policy in {"resample", "reverse"} and speed:
                        sample_count(speed, doc.sample_rate)
                total += len(mapping.segments) * visit(node["inputs"][clip.source]["target"])
        if not count or count > 64 or total > 256:
            raise CapabilityUnsupported("当前音频预览需要 1–64 段映射，嵌套求值最多 256 次")
        active.remove(key)
        costs[key] = total
        return total

    visit(root)


def tempo_filters(speed):
    """Keep every atempo stage within [0.5, 2], preserving pitch."""
    filters = []
    while speed > 2:
        filters.append("atempo=2")
        speed /= 2
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed *= 2
    filters.append(f"atempo={float(speed):.17g}")
    return filters


def render_segment(source, segment, policy, doc, allocate, canceled):
    from app.modules.generation import media_engine

    rate = doc.sample_rate
    speed = value(segment.rate)
    length = value(segment.local.end) - value(segment.local.start)
    wanted = sample_count(length, rate)
    origin = value(segment.source_start)
    finish = origin + length * speed
    start = sample_count(min(origin, finish), rate)
    # A muted hold still validates that the pinned source position exists.
    end = sample_count(max(origin, finish), rate) if speed else start + 1
    decoded = allocate()
    media_engine.run(
        [
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            f"aresample={rate},atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS",
            "-ar",
            str(rate),
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
            raise CapabilityUnsupported("映射范围超出源声音，不能静默补齐缺失素材")
    filters = []
    if policy == "mute":
        filters.append("volume=0")
    else:
        if speed < 0:
            filters.append("areverse")
        if abs(speed) != 1:
            if policy == "stretch":
                filters.extend(tempo_filters(abs(speed)))
            else:
                filters.extend([f"asetrate={sample_count(abs(speed), rate)}", f"aresample={rate}"])
    if filters and policy != "mute":
        processed = allocate()
        media_engine.run(
            [
                "-i",
                str(decoded),
                "-af",
                ",".join(filters),
                "-ar",
                str(rate),
                "-ac",
                str(doc.channels),
                "-c:a",
                "pcm_s16le",
                str(processed),
            ],
            canceled,
            timeout=300,
        )
        with wave.open(str(processed), "rb") as audio:
            produced = audio.getnframes()
        tolerance = min(rate // 20, wanted // 10)
        if not produced or abs(produced - wanted) > tolerance:
            raise CapabilityUnsupported("此片段无法在允许的时间误差内变速，请延长片段或调整速率")
        decoded, filters = processed, []
    # Temporal DSP can have a short tail; the requested edit interval is exact.
    # Source coverage and DSP length were verified before bounded tail fitting.
    filters.extend(
        [f"apad=whole_len={wanted}", f"atrim=end_sample={wanted}", "asetpts=PTS-STARTPTS"]
    )
    result = allocate()
    media_engine.run(
        [
            "-i",
            str(decoded),
            "-af",
            ",".join(filters),
            "-ar",
            str(rate),
            "-ac",
            str(doc.channels),
            "-c:a",
            "pcm_s16le",
            str(result),
        ],
        canceled,
        timeout=300,
    )
    with wave.open(str(result), "rb") as audio:
        if audio.getnframes() != wanted:
            raise Conflict("音频时间映射输出长度不一致")
    return result, wanted


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
            doc = read_document(node)
            clips, duration = [], 0
            for track in doc.tracks:
                for clip in track.clips:
                    source = render(node["inputs"][clip.source]["target"])
                    offset = sample_count(value(clip.start), doc.sample_rate)
                    gain = (
                        0
                        if track.muted or clip.muted
                        else 10 ** ((track.gain_db + clip.gain_db) / 20)
                    )
                    for segment in clip.source_map.segments:
                        decoded, count = render_segment(
                            source, segment, clip.source_map.audio_policy, doc, allocate, canceled
                        )
                        placed = offset + sample_count(value(segment.local.start), doc.sample_rate)
                        clips.append((decoded, placed, gain))
                        duration = max(duration, placed + count)
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
