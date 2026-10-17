"""Display names never mutate source filenames, including migration of old defaults."""

# ruff: noqa: F811
import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models.library import LibraryMedia, MediaVersion
from app.modules.library.media_types import display_name
from tests.test_media_workflow import studio  # noqa: F401


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("雨夜.mp4", "雨夜"),
        ("片段.v2.MP4", "片段.v2"),
        ("照片.JPEG", "照片"),
        ("配乐.wav", "配乐"),
        ("  留白.png  ", "留白"),
        ("场景.v2", "场景.v2"),
    ],
)
def test_default_name(filename, expected):
    assert display_name(filename) == expected


def test_upload_uses_display_name_and_keeps_filename(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/library"
    response = client.post(
        root + "/uploads", json=dict(filename="雨夜.v2.MP4", size_bytes=100, fingerprint="a" * 64)
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["filename"] == "雨夜.v2.MP4"
    item = client.get(root + "/items/" + result["media_id"]).json()
    assert item["name"] == "雨夜.v2"
    assert item["versions"][0]["filename"] == "雨夜.v2.MP4"


def test_migration_only_changes_untouched_defaults(studio):
    _, sessions, ids = studio
    spec = importlib.util.spec_from_file_location(
        "display_names", Path("alembic/versions/0037_material_display_names.py")
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sessions() as db:
        schema = db.connection().get_execution_options()["schema_translate_map"][None]
        assert schema.startswith("media_test_") or schema.startswith("test_")
        db.execute(text('SET LOCAL search_path TO "' + schema + '"'))
        rows = []
        for name, filename, revision in [
            ("雨夜.mp4", "雨夜.mp4", 1),
            ("手工.mp4", "手工.mp4", 2),
            ("新名称", "原名.png", 1),
            ("版本.v2.MP4", "版本.v2.MP4", 1),
        ]:
            m = LibraryMedia(
                project_id=uuid.UUID(ids["project"]), name=name, metadata_revision=revision
            )
            db.add(m)
            db.flush()
            v = MediaVersion(
                media_id=m.id,
                ordinal=1,
                filename=filename,
                media_type="video",
                size_bytes=100,
                fingerprint="a" * 64,
            )
            db.add(v)
            db.flush()
            rows.append((m.id, v.id, filename))
        with Operations.context(MigrationContext.configure(db.connection())):
            migration.upgrade()
        db.expire_all()
        assert [db.get(LibraryMedia, m).name for m, _, _ in rows] == [
            "雨夜",
            "手工.mp4",
            "新名称",
            "版本.v2",
        ]
        assert [db.get(LibraryMedia, m).metadata_revision for m, _, _ in rows] == [2, 2, 1, 2]
        for _, v, filename in rows:
            assert db.get(MediaVersion, v).filename == filename
        with Operations.context(MigrationContext.configure(db.connection())):
            migration.upgrade()
        db.expire_all()
        assert db.get(LibraryMedia, rows[0][0]).metadata_revision == 2
