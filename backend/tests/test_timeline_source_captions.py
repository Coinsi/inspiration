"""Clip-relative caption mapping accounts for trims, repeats and transition overlap."""

# ruff: noqa: F811
import uuid

from app.models.media_index import MediaAnnotation
from tests.test_library_materialization import seed
from tests.test_media_workflow import changing_video, post_ok, studio  # noqa: F401


def test_caption_preview_is_read_only_and_maps_reviewed_sources(studio, changing_video):
    client, sessions, ids = studio
    _, version, _ = seed(sessions, ids, changing_video)
    root = f"/api/v1/projects/{ids['project']}"
    usage = post_ok(
        client,
        root + "/library/usages",
        {"version_id": version, "shot_id": ids["shot"], "start_ms": 0, "end_ms": 2000},
    )
    job = post_ok(
        client,
        root + f"/library/usages/{usage['id']}/materialize",
        {"request_key": str(uuid.uuid4()), "output_type": "video"},
    )
    assert job["status"] == "succeeded"
    generation = client.get(root + f"/generations?target_type=shot&target_id={ids['shot']}").json()[
        0
    ]
    with sessions() as db:
        for start, end, text, kind, source in [
            (0, 800, "第一句", "speech", {"reviewed": True, "transcription_id": str(uuid.uuid4())}),
            (
                1000,
                1800,
                "第二句",
                "speech",
                {"reviewed": True, "transcription_id": str(uuid.uuid4())},
            ),
            (0, 2000, "未经核对", "speech", {}),
            (
                0,
                2000,
                "画面描述",
                "visual",
                {"reviewed": True, "transcription_id": str(uuid.uuid4())},
            ),
        ]:
            db.add(
                MediaAnnotation(
                    version_id=uuid.UUID(version),
                    start_ms=start,
                    end_ms=end,
                    text=text,
                    kind=kind,
                    source=source,
                    created_by=ids["user"],
                )
            )
        db.commit()
    timeline = post_ok(client, root + "/timelines", {"name": "字幕映射"})
    base = root + f"/timelines/{timeline['id']}"
    clips = [
        {
            "shot_id": ids["shot"],
            "generation_id": generation["id"],
            "in_point_ms": 500,
            "out_point_ms": 1500,
            "duration_ms": 1000,
            "transition": {"type": "dissolve", "duration_ms": 200},
        },
        {
            "shot_id": ids["shot"],
            "generation_id": generation["id"],
            "in_point_ms": 1200,
            "out_point_ms": 1800,
            "duration_ms": 600,
        },
    ]
    result = post_ok(client, base + "/subtitles/from-sources", {"items": clips})
    assert result["matched_clips"] == 2 and result["skipped_clips"] == []
    assert [(c["start_ms"], c["end_ms"], c["text"]) for c in result["items"]] == [
        (0, 300, "第一句"),
        (500, 1000, "第二句"),
        (800, 1400, "第二句"),
    ]
    assert client.get(base + "/document").json()["subtitles"] == []
    saved = client.put(
        base + "/document",
        json={"revision": 0, "items": clips, "audio": [], "subtitles": result["items"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["subtitles"][2]["source"]["start_ms"] == 1200
    assert saved.json()["subtitles"][2]["source"]["version_id"] == version
    assert client.post(
        base + "/subtitles/from-sources",
        json={"items": [{**clips[0], "shot_id": str(uuid.uuid4())}]},
    ).status_code in (404, 422)
