"""Composite immutable visual layers before subtitles, retaining base audio."""


def compose(source, root, read_blob, visuals, width, height, duration_ms, canceled):
    from app.modules.generation.media_engine import Canceled, run, source_file
    from app.modules.timeline.schemas import VisualIn

    if len(visuals) > 12:
        raise ValueError("最多支持12段叠加画面")
    layers = sorted(
        (v for v in visuals if not v.get("hidden")), key=lambda v: (v["track"], v["start_ms"])
    )
    if not layers:
        return source
    args = ["-protocol_whitelist", "file,pipe", "-i", str(source)]
    filters = []
    previous = "0:v:0"
    for i, raw in enumerate(layers, 1):
        v = VisualIn.model_validate(raw)
        if v.start_ms + v.duration_ms > duration_ms:
            raise ValueError("叠加画面不能超出时间线时长")
        if canceled():
            raise Canceled()
        path = root / f"visual{i}"
        path = source_file(read_blob, raw["blob_hash"], path)
        args += (
            ["-loop", "1"] if raw["output_type"] == "image" else ["-ss", str(v.in_point_ms / 1000)]
        )
        args += ["-protocol_whitelist", "file,pipe", "-i", str(path)]
        w, h = max(2, round(width * v.width / 2) * 2), max(2, round(height * v.height / 2) * 2)
        sizing = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=rgba"
            if v.fit == "cover"
            else f"scale={w}:{h}:force_original_aspect_ratio=decrease,format=rgba,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0"
        )
        start, end = v.start_ms / 1000, (v.start_ms + v.duration_ms) / 1000
        filters.append(
            f"[{i}:v:0]fps=24,{sizing},setsar=1,trim=duration={v.duration_ms / 1000},"
            f"setpts=PTS-STARTPTS+{start}/TB,colorchannelmixer=aa={v.opacity}[layer{i}]"
        )
        filters.append(
            f"[{previous}][layer{i}]overlay=x={round(width * v.x)}:y={round(height * v.y)}:"
            f"eof_action=pass:repeatlast=0:enable='gte(t,{start})*lt(t,{end})'[composite{i}]"
        )
        previous = f"composite{i}"
    output = root / "film-visuals.mp4"
    args += [
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{previous}]",
        "-map",
        "0:a:0",
        "-c:a",
        "copy",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "2",
        "-t",
        str(duration_ms / 1000),
        "-movflags",
        "+faststart",
        str(output),
    ]
    run(args, canceled, timeout=max(180, duration_ms / 100))
    return output
