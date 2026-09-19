"""Local, bounded media operations. All ffmpeg inputs are trusted temporary files."""

import io
import os
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


class Canceled(Exception):
    pass


def validate_output(data: bytes, output_type: str) -> tuple[bytes, dict]:
    """Decode provider outputs before accepting them as completed media."""
    if len(data) > 256 * 1024 * 1024:
        raise ValueError("供应商输出超过 256 MB")
    if output_type == "image":
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > 40_000_000:
                    raise ValueError("供应商图片超过 4000 万像素")
                source.load()
                actual = {"width": source.width, "height": source.height}
                # Persist real PNG bytes, including gateways returning JPEG in a b64 field.
                if source.format != "PNG":
                    buffer = io.BytesIO()
                    source.convert("RGBA" if "A" in source.getbands() else "RGB").save(
                        buffer, "PNG"
                    )
                    data = buffer.getvalue()
                return data, actual
        except (OSError, SyntaxError) as exc:
            raise ValueError("供应商返回的图片不能解码，未保存为成功结果") from exc
    with tempfile.TemporaryDirectory(prefix="inspiration-validate-") as tmp:
        path = Path(tmp) / "output.mp4"
        path.write_bytes(data)
        if data[4:8] != b"ftyp":
            raise ValueError("供应商没有返回有效 MP4，未保存为成功结果")
        run(
            [
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-t",
                "0.1",
                "-f",
                "null",
                "-",
            ],
            timeout=30,
        )
    return data, {}


def transform_image(data: bytes, options: dict) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 40_000_000:
            raise ValueError("图片超过 4000 万像素")
        has_alpha = "A" in source.getbands() or "transparency" in source.info
        im = ImageOps.exif_transpose(source).convert("RGBA" if has_alpha else "RGB")
        x, y, w, h = options.get("crop", [0, 0, 1, 1])
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.00001 or y + h > 1.00001:
            raise ValueError("裁剪区域必须在图片内")
        box = (
            round(x * im.width),
            round(y * im.height),
            round((x + w) * im.width),
            round((y + h) * im.height),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("裁剪区域太小")
        im = im.crop(box).rotate(-options.get("rotate", 0), expand=True)
        # Match the browser preview's brightness/contrast adjustment, preserve PNG alpha.
        alpha = im.getchannel("A") if has_alpha else None
        brightness, contrast = options.get("brightness", 1), options.get("contrast", 1)
        im = im.convert("RGB").point(
            lambda v: max(0, min(255, round((v * brightness - 127.5) * contrast + 127.5)))
        )
        if alpha is not None:
            im.putalpha(alpha)
        scale = options.get("scale", 1)
        size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
        if max(size) > 8192 or size[0] * size[1] > 40_000_000:
            raise ValueError("输出尺寸过大，请缩小倍率或裁剪区域")
        im = im.resize(size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def ffmpeg() -> str:
    import imageio_ffmpeg

    return os.environ.get("FFMPEG_BINARY") or imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str], canceled=lambda: False, timeout=180, cwd=None) -> str:
    # A file avoids pipe-buffer deadlocks during long encodes.
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(
            [ffmpeg(), "-hide_banner", "-nostdin", "-y", *args],
            stdout=subprocess.DEVNULL,
            stderr=log,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        start = time.monotonic()
        try:
            while proc.poll() is None:
                if canceled():
                    raise Canceled()
                if time.monotonic() - start > timeout:
                    raise ValueError("视频处理超时，请缩短时间线后重试")
                time.sleep(0.15)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        log.seek(0)
        text = log.read().decode("utf-8", errors="replace")
        if proc.returncode:
            raise ValueError("无法读取或合成媒体，请检查素材与截取时间。" + text[-500:])
        return text


def render(clips: list[dict], read_blob, options: dict, canceled=lambda: False) -> bytes:
    from app.modules.media.timing import duration

    duration_ms = duration(clips)
    if not clips or len(clips) > 100:
        raise ValueError("请选择 1–100 段素材")
    if duration_ms > 1_800_000:
        raise ValueError("时间线最长 30 分钟")
    height = int(options.get("height", 720))
    aspect = options.get("aspect_ratio", "16:9")
    dims = {
        "16:9": (height * 16 // 9, height),
        "9:16": (height, height * 16 // 9),
        "1:1": (height, height),
    }
    width, height = (n // 2 * 2 for n in dims[aspect])
    with tempfile.TemporaryDirectory(prefix="inspiration-render-") as tmp:
        root = Path(tmp)
        paths = []
        for i, clip in enumerate(clips):
            if canceled():
                raise Canceled()
            src = root / f"source{i}.{'png' if clip['output_type'] == 'image' else 'mp4'}"
            src.write_bytes(read_blob(clip["blob_hash"]))
            seconds = clip["duration_ms"] / 1000
            start = clip.get("in_point_ms", 0) / 1000
            image = clip["output_type"] == "image"
            # Decode-probe catches corrupt media and detects whether to synthesize silence.
            probe = run(
                ["-protocol_whitelist", "file,pipe", "-i", str(src), "-t", "0", "-f", "null", "-"],
                canceled,
            )
            has_audio = not image and "Audio:" in probe and not options.get("mute", False)
            args = (["-loop", "1"] if image else ["-ss", str(start)]) + [
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(src),
            ]
            if not has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,"
                f"tpad=stop_mode=clone:stop_duration={seconds}"
            )
            dest = root / f"clip{i}.mp4"
            args += [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0" if has_audio else "1:a:0",
                "-t",
                str(seconds),
                "-vf",
                vf,
                "-af",
                "aresample=48000,apad",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-threads",
                "2",
                str(dest),
            ]
            run(args, canceled, timeout=max(180, seconds * 10))
            paths.append(dest)
        output = root / "film.mp4"
        if any(c.get("transition") for c in clips):
            join_transitions(paths, clips, output, duration_ms, canceled)
        else:
            listing = root / "clips.txt"
            listing.write_text("\n".join(f"file '{p.name}'" for p in paths), encoding="utf-8")
            run(
                [
                    "-f",
                    "concat",
                    "-safe",
                    "1",
                    "-i",
                    str(listing),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                canceled,
            )
        from app.modules.timeline.visuals import compose

        output = compose(
            output,
            root,
            read_blob,
            options.get("visuals", []),
            width,
            height,
            duration_ms,
            canceled,
        )
        return finish_tracks(output, root, read_blob, options, duration_ms, canceled).read_bytes()


def join_transitions(paths, clips, output, duration_ms, canceled):
    from app.modules.media.timing import TRANSITIONS

    args, filters = [], []
    for i, path in enumerate(paths):
        args += ["-i", str(path)]
        filters += [
            f"[{i}:v:0]setpts=PTS-STARTPTS,fps=24,settb=AVTB[v{i}]",
            f"[{i}:a:0]asetpts=PTS-STARTPTS[a{i}]",
        ]
    video, audio, elapsed = "v0", "a0", clips[0]["duration_ms"] / 1000
    for i in range(1, len(paths)):
        transition = clips[i - 1].get("transition")
        if transition:
            seconds = transition["duration_ms"] / 1000
            filters.append(
                f"[{video}][v{i}]xfade=transition={TRANSITIONS[transition['type']]}:"
                f"duration={seconds}:offset={elapsed - seconds}[mv{i}]"
            )
            filters.append(f"[{audio}][a{i}]acrossfade=d={seconds}:c1=tri:c2=tri[ma{i}]")
            elapsed -= seconds
        else:
            filters.append(f"[{video}][{audio}][v{i}][a{i}]concat=n=2:v=1:a=1[cv{i}][ma{i}]")
        if not transition:
            filters.append(f"[cv{i}]fps=24,settb=AVTB[mv{i}]")
        video, audio = f"mv{i}", f"ma{i}"
        elapsed += clips[i]["duration_ms"] / 1000
    args += [
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{video}]",
        "-map",
        f"[{audio}]",
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
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-t",
        str(duration_ms / 1000),
        "-movflags",
        "+faststart",
        str(output),
    ]
    run(args, canceled, timeout=max(180, duration_ms / 100))


def finish_tracks(source, root, read_blob, options, duration_ms, canceled):
    """Mix real audio and render/mux captions from the immutable submission snapshot."""
    from app.modules.media.subtitles import export_srt

    audio = (
        [] if options.get("mute") else [a for a in options.get("audio", []) if not a.get("muted")]
    )
    cues = options.get("subtitles", [])
    # The render policy is separate from cue data in the snapshot.
    policy = options.get("subtitle_mode", "burn")
    if policy == "none":
        cues = []
    if not audio and not cues:
        return source
    if len(audio) > 24:
        raise ValueError("最多支持24段独立音频")
    args = ["-protocol_whitelist", "file,pipe", "-i", str(source)]
    filters = []
    for n, track in enumerate(audio, 1):
        if canceled():
            raise Canceled()
        path = root / f"audio{n}"
        path.write_bytes(read_blob(track["blob_hash"]))
        args += [
            "-ss",
            str(track["in_point_ms"] / 1000),
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
        ]
        seconds = track["duration_ms"] / 1000
        chain = (
            f"[{n}:a:0]atrim=duration={seconds},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=channel_layouts=stereo,volume={track['gain_db']}dB"
        )
        if track["fade_in_ms"]:
            chain += f",afade=t=in:st=0:d={track['fade_in_ms'] / 1000}"
        if track["fade_out_ms"]:
            fade = track["fade_out_ms"] / 1000
            chain += f",afade=t=out:st={seconds - fade}:d={fade}"
        chain += f",adelay={track['start_ms']}|{track['start_ms']}[a{n}]"
        filters.append(chain)
    if audio:
        filters.append(
            "[0:a:0]"
            + "".join(f"[a{n}]" for n in range(1, len(audio) + 1))
            + f"amix=inputs={len(audio) + 1}:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95:level=false:latency=true[mix]"
        )
    if cues:
        (root / "subtitles.srt").write_text(export_srt(cues), encoding="utf-8")
        if policy == "burn":
            # Fixed relative filename and cwd avoid Windows drive/filter escaping and user paths.
            filters.append(
                "[0:v:0]subtitles=filename=subtitles.srt:force_style='FontSize=22,Outline=1,MarginV=24'[captions]"
            )
        elif policy == "track":
            args += ["-f", "srt", "-i", str(root / "subtitles.srt")]
    if filters:
        args += ["-filter_complex", ";".join(filters)]
    burn = bool(cues and policy == "burn")
    args += ["-map", "[captions]" if burn else "0:v:0", "-map", "[mix]" if audio else "0:a:0"]
    if cues and policy == "track":
        args += [
            "-map",
            f"{len(audio) + 1}:s:0",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=zho",
            "-disposition:s:0",
            "default",
        ]
    args += ["-c:v", "libx264" if burn else "copy"]
    if burn:
        args += ["-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "2"]
    output = root / "film-tracks.mp4"
    args += [
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-t",
        str(duration_ms / 1000),
        "-movflags",
        "+faststart",
        str(output),
    ]
    run(args, canceled, timeout=max(180, duration_ms / 100), cwd=root)
    return output


def mock_image(index=0) -> bytes:
    im = Image.new("RGB", (640, 360), (32 + index * 15, 48, 62))
    draw = ImageDraw.Draw(im)
    draw.rectangle((30, 30, 610, 330), outline=(100, 130, 150), width=2)
    draw.text((65, 155), f"MOCK / TEST MEDIA   {index + 1}", fill="white", font_size=28)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()
