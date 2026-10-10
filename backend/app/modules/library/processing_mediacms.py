"""Reconcile actual MediaCMS jobs and import validated derivatives into our CAS.

The upstream copy is temporary. Checkpoints are committed before deletion, so a
lost response or process restart never needs to upload the original again.
"""

import json
import tempfile
import time
from pathlib import Path

from PIL import Image
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.errors import ProviderError
from app.models.identity import Project
from app.models.media_index import MediaAnnotation
from app.models.media_processing import MediaProcessingRun
from app.models.storage import Blob
from app.modules.library.media_ingest import probe_media
from app.modules.library.providers.media_files import media_filename
from app.modules.library.providers.mediacms import (
    MediaCMSCancelled,
    MediaCMSClient,
    MediaCMSError,
)
from app.storage import cas


def client():
    return MediaCMSClient(
        settings.library_mediacms_url,
        settings.library_mediacms_token,
        private_workflow_confirmed=True,
    )


def checkpoint(db, run, **values):
    # Update only our checkpoint columns; leave concurrent cancellation untouched.
    run.outputs = {**run.outputs, **values}
    db.commit()
    db.refresh(run)


def collect_file(db, remote, item, *, kind, version, stopped):
    from app.modules.library.processing_worker import check_capacity

    size = item.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ProviderError("媒体处理服务返回无效的产物大小")
    check_capacity(type("OutputSize", (), {"size_bytes": size})())
    with tempfile.TemporaryDirectory(
        prefix="mediacms-output-",
        dir=settings.library_processing_temp_dir,
    ) as temp:
        audio = version.media_type == "audio"
        target = Path(temp) / (
            ("proxy.m4a" if audio else "proxy.mp4") if kind == "proxy" else "poster.jpg"
        )
        remote.download(item["url"], target, max_bytes=size, canceled=stopped)
        if target.stat().st_size != size:
            raise ProviderError("媒体处理产物长度不完整")
        if kind == "proxy":
            info = probe_media(target, version.media_type, stopped)
            if abs(info["duration_ms"] - version.duration_ms) > 1500:
                raise ProviderError("转码产物与原素材时长不一致")
            mime = "audio/mp4" if audio else "video/mp4"
            if item.get("mime") != mime:
                raise ProviderError("转码服务未提供浏览器可播放的预览文件")
            return cas.put_file(
                db, target, mime, **{k: info[k] for k in ("duration_ms", "width", "height")}
            ).hash
        with Image.open(target) as image:
            image.verify()
        with Image.open(target) as image:
            image.thumbnail((1280, 1280))
            converted = Path(temp) / "verified.jpg"
            image.convert("RGB").save(converted, format="JPEG", quality=90)
            return cas.put_file(
                db, converted, "image/jpeg", width=image.width, height=image.height
            ).hash


def transcript_blob(db, data, version):
    cues = data.get("cues")
    if not isinstance(cues, list) or len(cues) > 20000:
        raise ProviderError("转写结果缺少有效的时间轴字幕")
    clean = []
    for cue in cues:
        if not isinstance(cue, dict):
            raise ProviderError("转写结果格式无效")
        start, end, text = cue.get("start_ms"), cue.get("end_ms"), cue.get("text")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= version.duration_ms + 1000
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > 4000
        ):
            raise ProviderError("转写字幕时间范围或文本无效")
        end = min(end, version.duration_ms)
        if start >= end:
            raise ProviderError("转写字幕超出视频时长")
        clean.append(
            {
                "start_ms": start,
                "end_ms": end,
                "text": text.strip(),
                "language": str(cue.get("language", "und"))[:40],
            }
        )
    payload = json.dumps(clean, ensure_ascii=False).encode()
    if len(payload) > 8 * 1024 * 1024:
        raise ProviderError("转写结果超过导入上限")
    return cas.put_bytes(db, payload, "application/json").hash


def poll_cleanup(remote, identity):
    """Do not unlink sources while upstream FFmpeg/Whisper still needs them.

    Cancellation stops subsequent phases and waits for the current upstream
    step. This integration does not claim to terminate remote child processes.
    """
    if remote.get_media(identity, missing=True) is None:
        return True
    if not remote.manifest(identity)["processing"]["complete"]:
        return False
    if remote.get_transcription(identity)["status"] in ("pending", "running"):
        return False
    remote.delete_media(identity)
    return True


def poll_mediacms(db, run, version, project_id, stopped):
    from app.modules.library.processing_worker import check_capacity

    last_check, cancel_seen = 0.0, False

    def interrupted():
        nonlocal last_check, cancel_seen
        if stopped():
            return True
        if time.monotonic() - last_check < 0.5:
            return cancel_seen
        # Independent read: long transfers should observe a browser cancellation.
        from app.modules.library.processing_worker import SessionLocal

        with SessionLocal() as state:
            cancel_seen = bool(
                state.scalar(
                    select(MediaProcessingRun.cancel_requested).where(
                        MediaProcessingRun.id == run.id,
                    )
                )
            )
        last_check = time.monotonic()
        return cancel_seen

    try:
        with client() as remote:
            identity = remote.upload_remote_id(str(run.id))
            run.remote_id = identity
            if run.cancel_requested:
                if not poll_cleanup(remote, identity):
                    return {"status": "waiting", "phase": "canceling"}
                checkpoint(db, run, upstream_removed=True)
                return {"status": "canceled"}
            # Output checkpoint makes cleanup retries independent of upstream data.
            if run.outputs.get("import_complete"):
                if not run.outputs.get("upstream_removed"):
                    if not poll_cleanup(remote, identity):
                        return {"status": "waiting", "phase": "publishing"}
                    checkpoint(db, run, upstream_removed=True)
                return {"status": "ready", "phase": "ready"}
            media = remote.get_media(identity, missing=True)
            if media is None:
                blob = db.get(Blob, run.source_hash)
                if blob is None:
                    raise ProviderError("原始素材文件不存在")
                check_capacity(blob)
                with cas.local_file(
                    blob, interrupted, temp_dir=settings.library_processing_temp_dir
                ) as path:
                    if interrupted():
                        return {"status": "waiting", "phase": "interrupted"}
                    media = remote.upload(
                        path,
                        title=f"Library {version.id}",
                        upload_key=str(run.id),
                        source_sha256=blob.hash,
                        mime_type=blob.mime,
                        filename=media_filename(version.id, blob.mime),
                        canceled=interrupted,
                    )
                checkpoint(db, run, uploaded=True)
            # The upstream media status becomes success after the first rendition.
            # Wait for the bridge's immutable profile snapshot and dispatch barrier
            # before importing/deleting, including speech-only video jobs.
            manifest = remote.manifest(identity)
            progress = manifest["processing"]
            if not progress["complete"]:
                return {"status": "waiting", "phase": "transcoding"}
            if progress["status"] == "fail":
                if run.outputs.get("dispatch_retry", run.status == "queued"):
                    remote.request_encode(identity)
                    return {"status": "waiting", "phase": "transcoding"}
                raise ProviderError("素材转码失败，请检查处理服务后重试")
            if run.outputs.get("encoding_completion") != progress:
                checkpoint(db, run, encoding_completion=progress)
            if "preview" in run.capabilities:
                encodings = [
                    item
                    for item in manifest["encodings"]
                    if item.get("codec") == ("aac" if version.media_type == "audio" else "h264")
                    and item.get("mime")
                    == ("audio/mp4" if version.media_type == "audio" else "video/mp4")
                    and str(item.get("resolution")) != "0-original"
                ]
                if not encodings:
                    raise ProviderError("素材转码已结束，但未生成可导入的预览，请检查转码配置")
                # Choose a bounded preview profile instead of downloading every rendition.
                encodings.sort(
                    key=lambda item: (
                        str(item.get("resolution")) not in ("720", "720p"),
                        item.get("size_bytes", 0),
                    )
                )
                if not run.outputs.get("proxy_hash"):
                    digest = collect_file(
                        db, remote, encodings[0], kind="proxy", version=version, stopped=interrupted
                    )
                    checkpoint(db, run, proxy_hash=digest)
                poster = (
                    (manifest.get("poster") or manifest.get("thumbnail"))
                    if version.media_type == "video"
                    else None
                )
                if poster and not run.outputs.get("poster_hash"):
                    digest = collect_file(
                        db, remote, poster, kind="poster", version=version, stopped=interrupted
                    )
                    checkpoint(db, run, poster_hash=digest)
            if "speech" in run.capabilities and not run.outputs.get("transcript_hash"):
                transcription = remote.get_transcription(identity)
                if transcription["status"] == "not_requested":
                    remote.request_transcription(identity)
                    return {"status": "waiting", "phase": "transcribing"}
                if transcription["status"] == "fail":
                    if run.outputs.get("dispatch_retry", run.status == "queued"):
                        remote.request_transcription(identity, retry=True)
                        return {"status": "waiting", "phase": "transcribing"}
                    raise ProviderError("语音转写失败，请检查音轨及转写模型后重试")
                if transcription["status"] != "success":
                    return {"status": "waiting", "phase": "transcribing"}
                checkpoint(db, run, transcript_hash=transcript_blob(db, transcription, version))
            checkpoint(db, run, import_complete=True)
            if run.cancel_requested or stopped():
                return {
                    "status": "waiting",
                    "phase": "canceling" if run.cancel_requested else "interrupted",
                }
            if not poll_cleanup(remote, identity):
                return {"status": "waiting", "phase": "publishing"}
            checkpoint(db, run, upstream_removed=True)
            return {"status": "ready", "phase": "ready"}
    except MediaCMSCancelled:
        raise
    except MediaCMSError as exc:
        raise ProviderError(
            "MediaCMS 处理失败，请检查服务连接、转码及转写配置后重试",
            {
                "retryable": exc.retryable,
            },
        ) from exc


def publish(db, run, version, project_id):
    """Caller holds the media lock and has rechecked cancellation and ownership."""
    if run.outputs.get("proxy_hash"):
        version.proxy_hash = run.outputs["proxy_hash"]
    if run.outputs.get("poster_hash"):
        version.poster_hash = run.outputs["poster_hash"]
    if run.outputs.get("transcript_hash"):
        blob = db.get(Blob, run.outputs["transcript_hash"])
        cues = json.loads(cas.read_bytes(blob))
        # Replace only a previous machine transcript; keep human/imported annotations.
        db.execute(
            delete(MediaAnnotation).where(
                MediaAnnotation.version_id == version.id,
                MediaAnnotation.source["provider"].astext == "mediacms",
            )
        )
        project = db.get(Project, project_id)
        user_id = run.created_by or project.owner_id
        db.add_all(
            [
                MediaAnnotation(
                    version_id=version.id,
                    created_by=user_id,
                    kind="speech",
                    **{k: cue[k] for k in ("start_ms", "end_ms", "text")},
                    source={
                        "provider": "mediacms",
                        "run_id": str(run.id),
                        "language": cue["language"],
                    },
                )
                for cue in cues
            ]
        )
