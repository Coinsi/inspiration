"""Actual schema-local GIN, unchanged substring semantics and permissions."""

# ruff: noqa: F811

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, inspect, select, text, update

from alembic import command
from app.models.annotation_text_index import FUNCTION_NAME, INDEX_NAME, AnnotationBigrams
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaAnnotation
from tests.test_library_migrations import (  # noqa: F401
    library_migration_schema,
    seed_legacy_library,
)
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture
def annotation_library(studio):
    client, sessions, ids = studio
    with sessions() as db:
        versions = []
        for name, project, deleted in (
            ("Visible", ids["project"], False),
            ("Other project", ids["other"], False),
            ("Trashed", ids["project"], True),
        ):
            media = LibraryMedia(
                project_id=project,
                name=name,
                deleted_at=datetime.now(UTC) if deleted else None,
            )
            db.add(media)
            db.flush()
            version = MediaVersion(
                media_id=media.id,
                ordinal=1,
                filename="test.wav",
                fingerprint="a" * 64,
                size_bytes=1,
                status="ready",
                media_type="audio",
                duration_ms=10000,
            )
            db.add(version)
            db.flush()
            versions.append(version.id)
        phrases = [
            "一只小鸟在窗外唱歌。",
            "雨滴落在森林里。",
            "A LITTLE Bird is singing.",
            "literal 50%_discount / and symbols",
            "not 50Xdiscount",
            "abXbc",  # both query grams exist, but the substring abc does not
            "abc at the beginning",
            "ab only once",
            "🙂鸟和Unicode İX, Straße, Σσς",
            "white  cup and a tab\there",
        ]
        for version in versions:
            for phrase in phrases:
                db.add(
                    MediaAnnotation(
                        version_id=version,
                        start_ms=0,
                        end_ms=1000,
                        kind="speech",
                        text=phrase,
                        created_by=ids["user"],
                    )
                )
        db.add(
            MediaAnnotation(
                version_id=versions[0],
                start_ms=1000,
                end_ms=2000,
                kind="visual",
                text="一只小鸟在窗外唱歌。",
                created_by=ids["user"],
            )
        )
        db.commit()
    return client, sessions, ids, versions


def test_create_all_keeps_functions_and_index_in_own_schema(annotation_library):
    _, sessions, _, _ = annotation_library
    with sessions() as db:
        connection = db.connection()
        schema = connection.schema_for_object(MediaAnnotation.__table__)
        assert schema.startswith("test_media_")
        assert INDEX_NAME in {
            index["name"]
            for index in inspect(connection).get_indexes("media_annotation", schema=schema)
        }
        details = db.execute(
            text("""
            SELECT p.provolatile, p.proparallel, pg_get_indexdef(i.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            JOIN pg_class i ON i.relnamespace = n.oid AND i.relname = :index_name
            WHERE n.nspname = :schema AND p.proname = :function_name
        """),
            {"schema": schema, "function_name": FUNCTION_NAME, "index_name": INDEX_NAME},
        ).one()
        assert details.provolatile == "i" and details.proparallel == "s"
        assert "USING gin" in details[2] and schema + "." + FUNCTION_NAME in details[2]
        grams = AnnotationBigrams("小鸟小鸟", table=MediaAnnotation.__table__)
        assert set(db.scalar(select(grams))) == {"小鸟", "鸟小"}
        assert db.scalar(select(AnnotationBigrams("A", table=MediaAnnotation.__table__))) == []


def test_indexed_search_preserves_exact_semantics_and_scope(annotation_library):
    client, sessions, ids, versions = annotation_library
    base = f"/api/v1/projects/{ids['project']}/library/search"
    queries = [
        "小鸟",
        "鸟",
        "小鸟在窗外",
        "窗内",
        "LITTLE BIRD",
        "50%_",
        "50%",
        "_",
        "%",
        "abc",
        "abab",
        "🙂鸟",
        "İX",
        "Straße",
        "Σσ",
        "white  cup",
        "\there",
        "",
        " 小鸟 ",
    ]
    with sessions() as db:
        for query in queries:
            statement = select(MediaAnnotation.id).where(
                MediaAnnotation.version_id == versions[0],
                MediaAnnotation.kind == "speech",
            )
            if query.strip():
                statement = statement.where(
                    MediaAnnotation.text.icontains(query.strip(), autoescape=True)
                )
            expected = {str(item) for item in db.scalars(statement)}
            response = client.post(
                base, json={"mode": "annotated", "kind": "speech", "query": query}
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert {item["id"] for item in result["items"]} == expected, query
            assert result["total"] == len(expected)
            assert all(item["version_id"] == str(versions[0]) for item in result["items"])
    denied = client.post(
        f"/api/v1/projects/{ids['other']}/library/search",
        json={"mode": "annotated", "query": "小鸟"},
    )
    assert denied.status_code == 403


def test_index_tracks_database_text_updates_and_deletes(annotation_library):
    client, sessions, ids, versions = annotation_library
    endpoint = f"/api/v1/projects/{ids['project']}/library/search"
    with sessions() as db:
        row_id = db.scalar(
            select(MediaAnnotation.id).where(
                MediaAnnotation.version_id == versions[0],
                MediaAnnotation.text == "雨滴落在森林里。",
            )
        )
        db.execute(
            update(MediaAnnotation)
            .where(MediaAnnotation.id == row_id)
            .values(text="夜里萤火虫飞舞")
        )
        db.commit()
    for query, expected in [("雨滴", 0), ("萤火", 1), ("萤火虫", 1)]:
        result = client.post(endpoint, json={"mode": "annotated", "query": query}).json()
        assert result["total"] == expected
    with sessions() as db:
        db.execute(delete(MediaAnnotation).where(MediaAnnotation.id == row_id))
        db.commit()
    assert client.post(endpoint, json={"mode": "annotated", "query": "萤火"}).json()["total"] == 0


def test_migration_indexes_existing_annotations_and_downgrades_in_order(library_migration_schema):
    connection, config, schema = library_migration_schema
    ids = seed_legacy_library(connection, schema)
    creator = connection.scalar(text('SELECT id FROM "user" LIMIT 1'))
    row_id = uuid.uuid4()
    connection.execute(
        text("""
        INSERT INTO media_annotation (id,version_id,start_ms,end_ms,kind,text,created_by)
        VALUES (:id,:version,0,1000,'speech','旧字幕小鸟歌唱',:creator)
    """),
        {"id": row_id, "version": ids["version"], "creator": creator},
    )
    command.upgrade(config, "0030_annotation_substring_index")
    assert INDEX_NAME in {
        index["name"]
        for index in inspect(connection).get_indexes("media_annotation", schema=schema)
    }
    count = connection.scalar(
        text(f"""
        SELECT count(*) FROM media_annotation
        WHERE {FUNCTION_NAME}(text) @> {FUNCTION_NAME}('小鸟') AND text ILIKE '%小鸟%'
    """)
    )
    assert count == 1
    assert (
        connection.scalar(
            text("""
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE p.proname=:name AND n.nspname=:schema
    """),
            {"name": FUNCTION_NAME, "schema": schema},
        )
        == 1
    )
    command.downgrade(config, "0029_media_queue_fairness")
    assert INDEX_NAME not in {
        index["name"]
        for index in inspect(connection).get_indexes("media_annotation", schema=schema)
    }
    assert (
        connection.scalar(
            text("""
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE p.proname=:name AND n.nspname=:schema
    """),
            {"name": FUNCTION_NAME, "schema": schema},
        )
        == 0
    )
    assert connection.scalar(text("SELECT count(*) FROM media_annotation")) == 1
    command.upgrade(config, "0030_annotation_substring_index")
    assert connection.scalar(text(f"SELECT {FUNCTION_NAME}('小鸟')")) == ["小鸟"]
