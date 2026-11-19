"""Small posters, display images and seekable browser videos. Originals stay addressable."""

import logging
import struct
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.database import SessionLocal
from app.models.storage import Blob, MediaRendition
from app.modules.generation import media_engine
from app.modules.library import media_ingest
from app.storage import cas

log = logging.getLogger(__name__)
QUEUE_LOCK = 726381913


def enqueue(db, blob):
    if blob.mime.startswith(("image/", "video/")):
        db.execute(
            insert(MediaRendition)
            .values(source_hash=blob.hash, priority=1)
            .on_conflict_do_nothing()
        )


def lookup(db, blob, view):
    """Return the prepared representation, or None while its worker is catching up."""
    image = blob.mime.startswith("image/")
    video = blob.mime.startswith("video/")
    if (
        not (image or video)
        or (view == "playback" and not video)
        or (view == "display" and not image)
    ):
        return None
    if image and blob.width and blob.height:
        edge = 480 if view == "thumbnail" else 1280
        if max(blob.width, blob.height) <= edge and blob.size_bytes <= edge * 160:
            return blob
    row = db.get(MediaRendition, blob.hash)
    if row is None:
        enqueue(db, blob)
        row = db.get(MediaRendition, blob.hash)
    field = {"thumbnail": "thumbnail_hash", "display": "display_hash", "playback": "playback_hash"}[
        view
    ]
    digest = getattr(row, field)
    if image and not digest and not row.attempts:
        # A custom cover may be opened before the worker reaches it. Decode once per hash.
        row = db.scalar(
            select(MediaRendition)
            .where(MediaRendition.source_hash == blob.hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not getattr(row, field) and not row.attempts:
            try:
                with (
                    db.begin_nested(),
                    tempfile.TemporaryDirectory(prefix="inspiration-cover-") as tmp,
                ):
                    with cas.local_file(blob, temp_dir=tmp) as source:
                        prepare_images(db, row, blob, source, Path(tmp), lambda: False)
                    row.due_at = None
            except (ValueError, OSError) as exc:
                row.attempts = 1
                row.due_at = datetime.now(UTC) + timedelta(minutes=2)
                log.warning("Could not prepare cover %s (%s)", blob.hash[:12], type(exc).__name__)
        digest = getattr(row, field)
    return db.get(Blob, digest) if digest else None


def fast_start(path):
    """Read top-level MP4 box headers only, including 64-bit box sizes."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        while (offset := stream.tell()) + 8 <= size:
            length, kind = struct.unpack(">I4s", stream.read(8))
            if length == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    break
                length = int.from_bytes(extended, "big")
            if length < stream.tell() - offset or offset + length > size:
                break
            if kind in (b"moov", b"mdat"):
                return kind == b"moov"
            stream.seek(offset + length)
    return False


def save(db, file):
    path, mime, metadata = file
    return cas.put_file(db, path, mime, **metadata).hash


def prepare_images(db, row, blob, source, folder, canceled):
    image, _ = media_ingest.read_image(source, canceled)
    with image:
        blob.width, blob.height = image.size
        # Resize the larger copy first so only one decoded image is kept in memory.
        for field, edge, quality in (("display_hash", 1280, 84), ("thumbnail_hash", 480, 78)):
            file = media_ingest.web_image(image, folder / (field + ".webp"), edge, quality)
            # Small, already-efficient originals may be cheaper than a re-encode.
            if max(blob.width, blob.height) <= edge and blob.size_bytes <= file[0].stat().st_size:
                setattr(row, field, blob.hash)
            else:
                setattr(row, field, save(db, file))


def prepare_video(db, row, blob, source, folder, canceled, *, playback):
    info = media_ingest.probe_media(source, "video", canceled)
    metadata = {k: info[k] for k in ("width", "height", "duration_ms")}
    for key, value in metadata.items():
        if getattr(blob, key) is None:
            setattr(blob, key, value)
    if not row.thumbnail_hash:
        row.thumbnail_hash = save(db, media_ingest.video_poster(source, folder, info, canceled))
    light_mp4 = (
        info["browser_mp4"]
        and max(info["width"], info["height"]) <= 1920
        and blob.size_bytes * 8000 / info["duration_ms"] <= 5_000_000
    )
    # Handle short compatible clips in one read. Hand longer work to the playback worker
    # so an encode cannot hold up the next user's posters.
    if not playback and (not light_mp4 or blob.size_bytes > 16 * 1024 * 1024):
        return
    if light_mp4 and fast_start(source):
        row.playback_hash = blob.hash
    elif light_mp4:
        output = folder / "playback.mp4"
        media_engine.run(
            [
                *media_ingest.input_options(info),
                "-i",
                str(source),
                "-map",
                "0:V:0",
                "-map",
                "0:a:0?",
                "-c",
                "copy",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-movflags",
                "+faststart",
                str(output),
            ],
            canceled=canceled,
            timeout=600,
        )
        row.playback_hash = save(db, (output, "video/mp4", metadata))
    else:
        row.playback_hash = save(db, media_ingest.build_playback(source, folder, info, canceled))


def process_next(db, canceled, *, playback=False):
    stage = (
        MediaRendition.thumbnail_hash.is_not(None)
        if playback
        else MediaRendition.thumbnail_hash.is_(None)
    )
    row = db.scalar(
        select(MediaRendition)
        .where(MediaRendition.due_at <= func.now(), stage)
        .order_by(MediaRendition.priority.desc(), MediaRendition.due_at, MediaRendition.source_hash)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        return False
    source_hash = row.source_hash
    try:
        blob = db.get(Blob, source_hash)
        with db.begin_nested(), tempfile.TemporaryDirectory(prefix="inspiration-preview-") as tmp:
            folder = Path(tmp)
            with cas.local_file(blob, canceled, temp_dir=folder) as source:
                if blob.mime.startswith("image/"):
                    prepare_images(db, row, blob, source, folder, canceled)
                    row.due_at = None
                else:
                    prepare_video(db, row, blob, source, folder, canceled, playback=playback)
                    row.due_at = None if row.playback_hash else datetime.now(UTC)
        row.attempts = 0
    except media_engine.Canceled:
        db.rollback()
        return False
    except Exception as exc:
        row.attempts += 1
        row.due_at = datetime.now(UTC) + timedelta(minutes=min(2 ** min(row.attempts, 11), 1440))
        log.warning("Media preview %s deferred (%s)", source_hash[:12], type(exc).__name__)
    db.commit()
    return True


def run_once(canceled=lambda: False, *, playback=False):
    # One worker per stage across all API processes.
    lock = QUEUE_LOCK + int(playback)
    with SessionLocal() as db:
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock}):
            return False
        return process_next(db, canceled, playback=playback)


def start():
    from app.modules.media import timeline_preview

    stop = threading.Event()

    def loop(process):
        while not stop.is_set():
            try:
                if process(stop.is_set):
                    continue
            except Exception:
                log.exception("Could not pick up media preview work")
            stop.wait(1.5)

    threads = [
        threading.Thread(target=loop, args=(run_once,), name="media-covers", daemon=True),
        threading.Thread(target=loop, args=(lambda cancel: run_once(cancel, playback=True),), name="media-playback", daemon=True),
        threading.Thread(target=loop, args=(timeline_preview.run_once,), name="timeline-previews", daemon=True),
    ]
    for thread in threads:
        thread.start()

    def shutdown():
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

    shutdown.is_alive = lambda: all(thread.is_alive() for thread in threads)
    return shutdown
