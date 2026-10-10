"""Audio rendition inside the existing supervised MediaCMS initialization job.

The pinned upstream does not transcode standalone audio. This bridge extension
uses FFmpeg and actual MediaCMS Encoding rows so cleanup follows its file hooks.
"""

import subprocess
import tempfile
from pathlib import Path

AUDIO_PROFILE = "Inspiration audio AAC"


def build_audio_preview(media):
    from django.conf import settings
    from django.core.files import File
    from files.models import EncodeProfile, Encoding, Media

    profile, _ = EncodeProfile.objects.get_or_create(
        name=AUDIO_PROFILE,
        defaults={"extension": "m4a", "codec": "aac", "resolution": 0, "active": False},
    )
    if (profile.extension, profile.codec, profile.resolution, profile.active) != (
        "m4a",
        "aac",
        0,
        False,
    ):
        raise ValueError("Unexpected bridge audio profile configuration")
    encoding, _ = Encoding.objects.get_or_create(
        media=media, profile=profile, chunk=False
    )
    if (
        encoding.status == "success"
        and encoding.media_file
        and Path(encoding.media_file.path).is_file()
    ):
        Media.objects.filter(pk=media.pk).update(encoding_status="success")
        return
    Encoding.objects.filter(pk=encoding.pk).update(status="running")
    with tempfile.TemporaryDirectory(
        prefix="audio-preview-", dir=settings.TEMP_DIRECTORY
    ) as folder:
        output, log = Path(folder) / "preview.m4a", Path(folder) / "ffmpeg.log"
        with log.open("wb") as errors:
            completed = subprocess.run(
                [
                    settings.FFMPEG_COMMAND,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-y",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    "wav,mp3,flac,ogg,mov",
                    "-i",
                    media.media_file.path,
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-map_metadata",
                    "-1",
                    "-map_chapters",
                    "-1",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "160k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-threads",
                    "2",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                timeout=6 * 3600,
                check=False,
            )
        if completed.returncode or not output.is_file() or not output.stat().st_size:
            Encoding.objects.filter(pk=encoding.pk).update(status="fail")
            raise ValueError("Audio preview encoding failed")
        encoding.status, encoding.progress = "success", 100
        with output.open("rb") as stream:
            encoding.media_file.save(f"{media.friendly_token}.m4a", File(stream))
        Media.objects.filter(pk=media.pk).update(encoding_status="success")
