"""Real FFmpeg/DB index lifecycle with deterministic inference contract fixtures."""

# ruff: noqa: F811
import uuid

import pytest

from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex, MediaSegment
from app.modules.library import index_worker, indexing
from tests.test_library import library, media_studio, upload  # noqa: F401
from tests.test_media_workflow import post_ok


def setup_index(library, monkeypatch):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    monkeypatch.setattr(index_worker, "SessionLocal", sessions)
    monkeypatch.setattr(
        indexing,
        "inference",
        lambda path, payload=None: (
            {"dimensions": 512, "model_key": "fixture-v1"}
            if path == "/health"
            else {"model_key": "fixture-v1", "embedding": [1.0] + [0.0] * 511}
        ),
    )
    return client, sessions, ids, base, upload(client, base, content)


@pytest.mark.parametrize("vector_enabled", [False, True])
def test_index_search_ownership_frames_and_annotation_sources(library, monkeypatch, vector_enabled):
    from app.core.config import settings

    monkeypatch.setattr(settings, "enable_pgvector", vector_enabled)
    client, sessions, ids, base, v = setup_index(library, monkeypatch)
    run = post_ok(client, f"{base}/versions/{v['id']}/index")
    assert post_ok(client, f"{base}/versions/{v['id']}/index")["id"] == run["id"]
    assert client.post(f"{base}/{v['media_id']}/trash").status_code == 409
    index_worker.process(uuid.UUID(run["id"]))
    state = client.get(f"{base}/index-status").json()
    assert state["indexed"] == 1 and state["runs"][v["id"]]["completed"] == 1
    result = post_ok(client, f"{base}/search", {"query": "moving test pattern"})
    assert len(result["items"]) == 1 and result["items"][0]["score"] == 1.0
    frame = result["items"][0]["thumbnail_hash"]
    assert client.get(f"/api/v1/projects/{ids['project']}/blobs/{frame}").status_code == 200
    assert (
        client.post(f"/api/v1/projects/{ids['other']}/library/versions/{v['id']}/index").status_code
        == 403
    )
    with sessions() as db:
        # An unauthorized other project's equally similar segment must never enter results.
        other = LibraryMedia(project_id=ids["other"], name="hidden")
        db.add(other)
        db.flush()
        ver = MediaVersion(
            media_id=other.id,
            ordinal=1,
            filename="hidden.mp4",
            fingerprint="a" * 64,
            size_bytes=1,
            status="ready",
            duration_ms=2000,
        )
        db.add(ver)
        db.flush()
        hidden = MediaIndex(
            version_id=ver.id, model_key="fixture-v1", status="ready", total=1, completed=1
        )
        db.add(hidden)
        db.flush()
        db.add(
            MediaSegment(
                index_id=hidden.id,
                ordinal=0,
                start_ms=0,
                end_ms=2000,
                frames=[],
                embedding=[1.0] + [0.0] * 511,
            )
        )
        db.commit()
    assert len(post_ok(client, f"{base}/search", {"query": "anything"})["items"]) == 1
    with sessions() as db:
        db.add(
            MediaSegment(
                index_id=uuid.UUID(run["id"]),
                ordinal=1,
                start_ms=1000,
                end_ms=1500,
                frames=[],
                embedding=[0.8, 0.6] + [0.0] * 510,
            )
        )
        db.commit()
    assert len(post_ok(client, f"{base}/search", {"query": "anything"})["items"]) == 2
    collapsed = post_ok(client, f"{base}/search", {"query": "anything", "collapse_versions": True})
    assert len(collapsed["items"]) == 1 and collapsed["items"][0]["score"] == pytest.approx(1)
    assert not post_ok(
        client, f"{base}/search", {"query": "anything", "collapse_versions": True, "offset": 1}
    )["items"]
    for kind in ["visual", "speech"]:
        post_ok(
            client,
            f"{base}/versions/{v['id']}/annotations",
            {"start_ms": 100, "end_ms": 1000, "kind": kind, "text": "有一只狗，100%可见"},
        )
    found = post_ok(
        client, f"{base}/search", {"query": "狗", "mode": "annotated", "kind": "visual", "limit": 1}
    )
    assert found["total"] == 1 and found["items"][0]["kind"] == "visual"
    assert post_ok(client, f"{base}/search", {"query": "%", "mode": "annotated"})["total"] == 2
    assert (
        client.post(
            f"{base}/versions/{v['id']}/annotations",
            json={"start_ms": 0, "end_ms": 3000, "text": "wrong"},
        ).status_code
        == 422
    )
    post_ok(client, f"{base}/{v['media_id']}/trash")
    assert post_ok(client, f"{base}/search", {"query": "anything"})["items"] == []
    assert post_ok(client, f"{base}/search", {"query": "狗", "mode": "annotated"})["items"] == []
    assert client.get(f"/api/v1/projects/{ids['project']}/blobs/{frame}").status_code == 404
    post_ok(client, f"{base}/{v['media_id']}/restore")
    assert len(post_ok(client, f"{base}/search", {"query": "anything"})["items"]) == 2


def test_failed_rebuild_keeps_previous_index_and_retry_resumes(library, monkeypatch):
    client, sessions, ids, base, v = setup_index(library, monkeypatch)
    old = post_ok(client, f"{base}/versions/{v['id']}/index")
    index_worker.process(uuid.UUID(old["id"]))
    newer = post_ok(client, f"{base}/versions/{v['id']}/index")
    real_embed = indexing.embed

    def fail(*args):
        raise ValueError("fixture outage")

    monkeypatch.setattr(indexing, "embed", fail)
    index_worker.process(uuid.UUID(newer["id"]))
    with sessions() as db:
        assert db.get(MediaIndex, uuid.UUID(newer["id"])).status == "failed"
    monkeypatch.setattr(indexing, "embed", real_embed)
    assert len(post_ok(client, f"{base}/search", {"query": "video"})["items"]) == 1
    retry = post_ok(client, f"{base}/versions/{v['id']}/index")
    assert retry["id"] == newer["id"]
    index_worker.process(uuid.UUID(retry["id"]))
    assert len(post_ok(client, f"{base}/search", {"query": "video"})["items"]) == 1
    last = post_ok(client, f"{base}/versions/{v['id']}/index")
    post_ok(client, f"{base}/indices/{last['id']}/cancel")
    index_worker.process(uuid.UUID(last["id"]))
    with sessions() as db:
        assert db.get(MediaIndex, uuid.UUID(last["id"])).status == "canceled"
    assert client.post(f"{base}/search", json={"image": "invalid"}).status_code == 422


def test_subtitles_import_atomic_dedup_and_encoding(library, monkeypatch):
    client, sessions, ids, base, v = setup_index(library, monkeypatch)
    from app.modules.media.subtitles import export_srt, parse_srt

    content = "\ufeff1\r\n00:00:00,100 --> 00:00:00,800\r\n你好，世界\r\n第二行\r\n\r\n2\r\n00:00:01,000 --> 00:00:01,900\r\n看见一只狗\r\n"
    cues = parse_srt(content, 2000)
    assert parse_srt(export_srt(cues), 2000) == cues
    path = f"{base}/versions/{v['id']}/subtitles"
    assert post_ok(client, path, {"content": content}) == {"added": 2, "skipped": 0}
    assert post_ok(client, path, {"content": content}) == {"added": 0, "skipped": 2}
    bad = content + "\n3\n00:00:05,000 --> 00:00:06,000\n不应部分导入"
    assert client.post(path, json={"content": bad}).status_code == 422
    assert post_ok(client, f"{base}/search", {"mode": "annotated", "kind": "speech"})["total"] == 2
    assert post_ok(client, f"{base}/search", {"mode": "annotated", "kind": "visual"})["total"] == 0
    annotation = client.get(f"{base}/versions/{v['id']}/annotations").json()["items"][0]
    assert client.delete(f"{base}/annotations/{annotation['id']}").status_code == 200
    assert post_ok(client, f"{base}/search", {"mode": "annotated", "kind": "speech"})["total"] == 1
    for bad in [
        "",
        "1\n00:61:00,100 --> 00:61:00,200\n错误",
        "1\n00:00:00,100 --> 00:00:00,100\n零时长",
        "\ufffd",
    ]:
        with pytest.raises(ValueError):
            parse_srt(bad)
