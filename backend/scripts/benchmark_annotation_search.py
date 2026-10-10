"""Disposable PostgreSQL subtitle benchmark; no model, network query or main-schema writes.

Run from backend: .venv/Scripts/python.exe scripts/benchmark_annotation_search.py --run
Only generated captions are used. SQL timings do not measure ASR quality or HTTP/network latency.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.schema import CreateSchema, DropSchema  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.annotation_text_index import INDEX_NAME, AnnotationBigrams  # noqa: E402
from app.models.identity import Project, User  # noqa: E402
from app.models.library import LibraryMedia, MediaVersion  # noqa: E402
from app.models.media_index import MediaAnnotation  # noqa: E402
from app.modules.library import indexing  # noqa: E402
from app.modules.library.schemas import MediaSearchIn  # noqa: E402


def node_names(plan):
    names = [plan.get("Index Name", "")]
    for child in plan.get("Plans", []):
        names.extend(node_names(child))
    return names


def explain(db, statement, parameters):
    compiled = statement.compile(
        dialect=db.bind.dialect,
        compile_kwargs={"literal_binds": True},
    )
    plan = db.execute(
        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(compiled)), parameters
    ).scalar_one()[0]
    return plan


def seed(db, rows):
    user = User(
        username="subtitle_benchmark",
        email="benchmark@example.invalid",
        display_name="Isolated benchmark",
    )
    db.add(user)
    db.flush()
    projects, version_ids = [], []
    for number in range(2):
        project = Project(code=f"BENCH-{number}", name="Subtitle benchmark", owner_id=user.id)
        db.add(project)
        db.flush()
        projects.append(project)
        for ordinal in range(20):
            media = LibraryMedia(project_id=project.id, name=f"Generated transcript {ordinal}")
            db.add(media)
            db.flush()
            version = MediaVersion(
                media_id=media.id,
                ordinal=1,
                filename="fixture.wav",
                size_bytes=1,
                fingerprint="0" * 64,
                status="ready",
                media_type="audio",
                duration_ms=3600000,
            )
            db.add(version)
            db.flush()
            version_ids.append(str(version.id))
    db.execute(
        text("""
        INSERT INTO media_annotation
          (id,version_id,start_ms,end_ms,kind,text,source,created_by)
        SELECT CAST(md5(:salt || i::text) AS uuid),
          (CAST(:versions AS uuid[]))[1 + i % 40], (i % 899) * 4000,
          (i % 899) * 4000 + 3500, 'speech',
          CASE WHEN i % 997 = 0 THEN '一只小鸟在窗边轻轻歌唱，LITTLE Bird sings.'
            ELSE (ARRAY[
              '镜头掠过山间清澈的溪流，画面里阳光渐渐亮起。',
              '演员推开木门走进房间，准备开始今天的工作。',
              '远处的街道响起脚步声，雨水顺着屋檐落下。',
              '画面里树叶随风摆动，桌上的咖啡还冒着热气。',
              '车辆缓慢驶过街角，路边有人交谈。',
              'A quiet river runs past the old house at sunset.'
            ])[1 + i % 6] END || ' 字幕编号' || i::text,
          '{}'::jsonb, CAST(:creator AS uuid)
        FROM generate_series(1, :rows) AS series(i)
    """),
        {"salt": uuid.uuid4().hex, "versions": version_ids, "creator": str(user.id), "rows": rows},
    )
    db.commit()
    return user, projects, version_ids


def benchmark(db, project):
    where = [
        LibraryMedia.project_id == project.id,
        LibraryMedia.deleted_at.is_(None),
        MediaAnnotation.kind == "speech",
    ]
    base = select(func.count()).select_from(MediaAnnotation).join(MediaVersion).join(LibraryMedia)
    results = []
    for kind, query in (
        ("two_character", "小鸟"),
        ("long_phrase", "小鸟在窗边轻轻歌唱"),
        ("no_match", "陨石坠落火山口"),
        ("english_case", "little BIRD"),
        ("common_two_character", "画面"),
        ("single_character_fallback", "鸟"),
    ):
        old = base.where(*where, MediaAnnotation.text.icontains(query, autoescape=True))
        new = old
        if len(query) >= 2:
            table = MediaAnnotation.__table__
            new = new.where(
                AnnotationBigrams(MediaAnnotation.text, table=table).contains(
                    AnnotationBigrams(query, table=table)
                )
            )
        old_hits, new_hits = db.scalar(old), db.scalar(new)
        if old_hits != new_hits:
            raise AssertionError("Indexed count changed substring semantics")
        baseline, indexed = explain(db, old, {}), explain(db, new, {})
        uses_gin = INDEX_NAME in node_names(indexed["Plan"])
        if kind in ("two_character", "long_phrase", "no_match") and not uses_gin:
            raise AssertionError("Selective query did not use the GIN index with default planner")
        started = time.monotonic()
        response = indexing.search(
            db,
            SimpleNamespace(project=project),
            MediaSearchIn(mode="annotated", kind="speech", query=query),
        )
        business_ms = (time.monotonic() - started) * 1000
        if response["total"] != old_hits:
            raise AssertionError("Business search count differs from the baseline")
        if any(
            str(item["version_id"]) not in db.info["allowed_versions"] for item in response["items"]
        ):
            raise AssertionError("Result escaped the requested project")
        results.append(
            {
                "kind": kind,
                "query": query,
                "hits": old_hits,
                "uses_gin": uses_gin,
                "baseline_explain": baseline,
                "indexed_explain": indexed,
                "business_search_ms": round(business_ms, 3),
                "business_returned": len(response["items"]),
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Create and clean a random benchmark schema"
    )
    parser.add_argument("--rows", type=int, default=100000)
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    if not 20000 <= args.rows <= 200000:
        parser.error("rows must be between 20000 and 200000")
    run_id = uuid.uuid4().hex
    schema = "test_annotation_bench_" + run_id
    output = Path(__file__).resolve().parents[2] / ".runtime" / "annotation-search" / run_id
    output.mkdir(parents=True, exist_ok=False)
    evidence = {
        "run_id": run_id,
        "rows": args.rows,
        "projects": 2,
        "versions": 40,
        "data": "Deterministic generated Chinese/English captions; not an ASR quality dataset",
        "limits": "Warm local PostgreSQL SQL/business timings; not HTTP/WAN or video inference",
        "verified": False,
        "schema_removed": False,
    }
    engine = create_engine(os.environ.get("TEST_DATABASE_URL") or settings.database_url)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        translated = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(translated, checkfirst=False)
        with Session(translated) as db:
            # Explain uses text(compiled SQL), so set this transaction's path to
            # the same private schema; ORM execution still uses schema translation.
            db.execute(text(f'SET search_path TO "{schema}"'))
            started = time.monotonic()
            _, projects, version_ids = seed(db, args.rows)
            evidence["seed_with_gin_seconds"] = round(time.monotonic() - started, 3)
            db.info["allowed_versions"] = set(version_ids[:20])
            db.execute(text("ANALYZE media_annotation"))
            db.execute(text("ANALYZE media_version"))
            db.execute(text("ANALYZE library_media"))
            evidence["postgres_version"] = db.scalar(text("SHOW server_version"))
            evidence["gin_bytes"] = db.scalar(
                text("SELECT pg_relation_size(:name)"), {"name": f"{schema}.{INDEX_NAME}"}
            )
            evidence["table_bytes"] = db.scalar(text("SELECT pg_relation_size('media_annotation')"))
            evidence["queries"] = benchmark(db, projects[0])
            evidence["verified"] = True
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
            evidence["schema_removed"] = not bool(
                connection.scalar(
                    text("SELECT 1 FROM pg_namespace WHERE nspname=:schema"),
                    {"schema": schema},
                )
            )
        engine.dispose()
        (output / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "verified": evidence["verified"],
                    "schema_removed": evidence["schema_removed"],
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
