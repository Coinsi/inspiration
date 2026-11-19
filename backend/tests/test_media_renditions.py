"""Browsing must be lightweight without changing source bytes, visibility, or Range semantics."""

# ruff: noqa: F811
import hashlib
import io
from pathlib import Path

from PIL import Image
from sqlalchemy import func, select

from app.models.identity import Project
from app.models.storage import Blob, MediaRendition
from app.modules.generation import media_engine
from app.modules.media import renditions
from app.storage import cas, retention
from tests.test_media_workflow import changing_video, studio  # noqa: F401


def publish(environment, data, mime):
    client, sessions, ids = environment
    with sessions() as db:
        blob = cas.put_bytes(db, data, mime)
        digest = blob.hash
        db.get(Project, ids["project"]).cover_blob_hash = digest
        db.commit()
    return client, sessions, ids, digest, f"/api/v1/projects/{ids['project']}/blobs/{digest}"


def test_cold_image_then_cached_preview_keeps_original_private(studio):
    buf = io.BytesIO()
    Image.effect_noise((1600, 900), 64).convert("RGB").save(buf, "PNG")
    original = buf.getvalue()
    client, sessions, ids, digest, url = publish(studio, original, "image/png")
    first = client.get(url, params={"view": "thumbnail"})
    assert first.status_code == 200 and first.headers["content-type"] == "image/webp"
    assert len(first.content) < len(original) // 10
    with Image.open(io.BytesIO(first.content)) as cover:
        assert cover.size == (480, 270)
    display = client.get(url, params={"view": "display"})
    with Image.open(io.BytesIO(display.content)) as image:
        assert image.size == (1280, 720)
    assert client.get(url + "?download=true&view=thumbnail").content == original
    assert (
        client.get(
            url + "?view=thumbnail", headers={"If-None-Match": first.headers["etag"]}
        ).status_code
        == 304
    )
    other = url.replace(str(ids["project"]), str(ids["other"]))
    assert (
        client.get(
            other + "?view=thumbnail", headers={"If-None-Match": first.headers["etag"]}
        ).status_code
        == 403
    )
    with sessions() as db:
        row = db.get(MediaRendition, digest)
        renditions.enqueue(db, db.get(Blob, digest))
        assert row.due_at is None and not renditions.process_next(db, lambda: False)
        assert db.scalar(select(func.count()).select_from(MediaRendition)) == 1
        assert retention.inspect_blob(db, row.thumbnail_hash)["retained"]
        db.get(Project, ids["project"]).cover_blob_hash = None
        db.flush()
        assert not retention.inspect_blob(db, digest)["retained"]


def test_old_video_gets_a_poster_and_fast_start_without_reencoding(
    studio, changing_video, tmp_path
):
    client, sessions, ids, digest, url = publish(studio, changing_video, "video/mp4")
    pending = client.get(url + "?view=thumbnail")
    assert pending.status_code == 503 and pending.headers["cache-control"] == "no-store"
    redirect = client.get(url + "?view=playback", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == url
    with sessions() as db:
        assert renditions.process_next(db, lambda: False)
        row = db.get(MediaRendition, digest)
        assert row.due_at is None and row.attempts == 0 and row.playback_hash != digest
        assert not renditions.fast_start(Path(db.get(Blob, digest).storage_uri))
        assert renditions.fast_start(Path(db.get(Blob, row.playback_hash).storage_uri))
        packet_hashes = []
        for name, media_hash in (("original", digest), ("playback", row.playback_hash)):
            result = tmp_path / f"{name}.sha256"
            media_engine.run(
                [
                    "-i",
                    db.get(Blob, media_hash).storage_uri,
                    "-map",
                    "0:v",
                    "-map",
                    "0:a",
                    "-c",
                    "copy",
                    "-f",
                    "streamhash",
                    str(result),
                ]
            )
            packet_hashes.append(result.read_text())
        assert packet_hashes[0] == packet_hashes[1]
        assert not renditions.process_next(db, lambda: False)
    cover = client.get(url + "?view=thumbnail")
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/webp"
    full = client.get(url + "?view=playback")
    part = client.get(url + "?view=playback", headers={"Range": "bytes=0-4095"})
    assert part.status_code == 206 and part.content == full.content[:4096]
    assert b"moov" in part.content[:64]
    assert (
        client.get(url + "?view=playback", headers={"Range": "bytes=-64"}).content
        == full.content[-64:]
    )
    assert (
        client.get(
            url + "?view=playback", headers={"Range": "bytes=0-31", "If-Range": '"stale"'}
        ).status_code
        == 200
    )
    assert hashlib.sha256(client.get(url).content).hexdigest() == digest
    assert (
        client.get(
            url.replace(str(ids["project"]), str(ids["other"])) + "?view=playback"
        ).status_code
        == 403
    )


def test_incompatible_video_publishes_cover_before_browser_transcode(studio, tmp_path):
    source = tmp_path / "source.mkv"
    media_engine.run(
        ["-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=1", "-c:v", "ffv1", str(source)]
    )
    client, sessions, _, digest, url = publish(studio, source.read_bytes(), "video/x-matroska")
    with sessions() as db:
        renditions.enqueue(db, db.get(Blob, digest))
        db.commit()
        assert renditions.process_next(db, lambda: False)
        row = db.get(MediaRendition, digest)
        assert row.thumbnail_hash and row.due_at and not row.playback_hash
        assert client.get(url + "?view=thumbnail").status_code == 200
        assert not renditions.process_next(db, lambda: False)
        assert renditions.process_next(db, lambda: False, playback=True)
        assert row.due_at is None
        playback = db.get(Blob, row.playback_hash)
        assert playback.mime == "video/mp4" and renditions.fast_start(Path(playback.storage_uri))
        assert not renditions.process_next(db, lambda: False, playback=True)
