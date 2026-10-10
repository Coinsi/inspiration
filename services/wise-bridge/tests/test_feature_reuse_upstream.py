"""Opt-in, real pinned WISE/SQLite/FAISS storage tests; no model is instantiated.

The small vectors are explicit storage fixtures, not generated-media or retrieval
quality evidence. Run with INSPIRATION_WISE_STORAGE_TESTS=1 in the upstream env.
"""

import datetime
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path


def build_fixture(root, sources):
    import numpy as np
    import sqlalchemy as sa
    from wise import db
    from wise.data_models import MediaType, ModalityType, SourceCollectionType
    from wise.feature.store.faiss_store import FaissStore
    from wise_bridge import MODELS
    from wise_bridge.upstream_compat import open_project

    root.parent.mkdir(parents=True, exist_ok=True)
    project = open_project(root, create_project=True)
    stores = {}
    object_cls = None
    if any(
        feature[1] == "object" for _, _, features in sources for feature in features
    ):
        from wise.feature.feature_extractor_factory import get_feature_extractor_class

        object_cls = get_feature_extractor_class(MODELS["object"])
        object_cls.create_vector_metadata_table(project.db_engine)
    with project.db_engine.begin() as conn, project.thumbsdb_engine.begin() as thumbs:
        collection = conn.execute(
            sa.insert(db.source_collections_table).values(
                location="/fixture-media", type=SourceCollectionType.DIR
            )
        ).inserted_primary_key[0]
        for sid, kind, features in sources:
            mid = conn.execute(
                sa.insert(db.media_table).values(
                    source_collection_id=collection,
                    path=sid + ".mp4",
                    checksum=hashlib.sha256(sid.encode()).digest(),
                    size_in_bytes=10,
                    date_modified=datetime.datetime(
                        2026, 1, 1, tzinfo=datetime.timezone.utc
                    ),
                    media_type=MediaType(kind),
                    format="mp4",
                    width=2,
                    height=2,
                    num_frames=2,
                    duration=0.0 if kind == "image" else 1.0,
                )
            ).inserted_primary_key[0]
            thumbs.execute(
                sa.insert(db.thumbnails_table).values(
                    media_id=mid,
                    timestamp=None if kind == "image" else 0.0,
                    content=b"owned thumbnail",
                )
            )
            for feature in features:
                modality, cap, time, value = feature[:4]
                modality, model = ModalityType(modality), MODELS[cap]
                vid = conn.execute(
                    sa.insert(db.vectors_table).values(
                        media_id=mid,
                        modality=modality,
                        feature_extractor_id=model,
                        timestamp=time,
                        end_timestamp=time + 0.5
                        if modality == ModalityType.AUDIO
                        else None,
                    )
                ).inserted_primary_key[0]
                conn.execute(sa.insert(db.featured_table).values(vector_id=vid))
                if cap == "object":
                    from wise.feature.feature_extractor import BBoxXYWH
                    from wise.feature.transformers_owlv2 import OWLv2FeatureMetadata

                    object_cls.add_to_vector_metadata_table(
                        conn,
                        [vid],
                        [
                            OWLv2FeatureMetadata(
                                bbox=BBoxXYWH(*feature[4]), objectness_score=0.8
                            )
                        ],
                    )
                if (modality, model) not in stores:
                    directory = project.create_features_dir(model)
                    store = FaissStore(modality, directory)
                    store.enable_write(shard_maxcount=2)
                    stores[modality, model] = store
                stores[modality, model].add([vid], np.array([value], dtype=np.float32))
    for store in stores.values():
        store.close()
    project.db_engine.dispose()
    project.thumbsdb_engine.dispose()
    return project


def bytes_digest(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def vector_snapshot(root):
    import sqlalchemy as sa
    from wise import db
    from wise.feature.store.faiss_store import FaissStore
    from wise_bridge.upstream_compat import open_project

    project = open_project(root)
    vectors = {}
    try:
        with project.db_engine.connect() as conn:
            metadata = {
                row.id: dict(row._mapping)
                for row in conn.execute(sa.select(db.vectors_table))
            }
        for modality, models in project.discover_assets().items():
            for model, asset in models.items():
                store = FaissStore(modality, asset["features_dir"])
                for ids, values in store.iter_batch(2):
                    for vid, value in zip(ids.tolist(), values.tolist()):
                        assert vid in metadata
                        assert metadata[vid]["modality"] == modality
                        assert metadata[vid]["feature_extractor_id"] == model
                        assert vid not in vectors
                        vectors[vid] = value
                store.close()
        assert set(metadata) == set(vectors)
        return metadata, vectors
    finally:
        project.db_engine.dispose()
        project.thumbsdb_engine.dispose()


def exercise_real_faiss_delete_and_image_delta(tmp_path):
    import sqlalchemy as sa
    from wise import db
    from wise._main.create_index import main as create_index
    from wise_bridge.feature_reuse import merge_delta, prune_project
    from wise_bridge.upstream_compat import open_project
    from wise_bridge.upstream_worker import inspect_project, verify_installation

    verify_installation()
    original = tmp_path / "published" / "engine"
    build_fixture(
        original,
        [
            (
                "keep",
                "video",
                [
                    ("video", "visual", 0.0, [1, 0, 0]),
                    ("audio", "audio", 0.0, [0, 1, 0]),
                ],
            ),
            ("remove", "image", [("image", "visual", None, [0, 0, 1])]),
        ],
    )
    original_hashes = bytes_digest(original)
    metadata_before, vectors_before = vector_snapshot(original)
    snapshot = tmp_path / "unpublished"
    engine, delta = snapshot / "engine", snapshot / "delta"
    shutil.copytree(original, engine)
    (engine / "unknown.note").write_text("must not be removed")
    result = prune_project(engine, ["keep", "remove"], ["keep"])
    assert result == {"retained_sources": 1, "removed_sources": 1}
    metadata_after, vectors_after = vector_snapshot(engine)
    assert len(metadata_after) == 2
    for vid, value in vectors_after.items():
        assert value == vectors_before[vid]
        assert metadata_after[vid] == metadata_before[vid]
    project = open_project(engine)
    with (
        project.db_engine.connect() as conn,
        project.thumbsdb_engine.connect() as thumbs,
    ):
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(db.featured_table)
            ).scalar()
            == 2
        )
        assert (
            thumbs.execute(
                sa.select(sa.func.count()).select_from(db.thumbnails_table)
            ).scalar()
            == 1
        )
    project.db_engine.dispose()
    project.thumbsdb_engine.dispose()
    build_fixture(
        delta, [("image", "image", [("image", "visual", None, [0.5, 0.5, 0])])]
    )
    delta_hashes = bytes_digest(delta)
    merge_delta(engine, delta, ["keep"], ["image"])
    metadata_final, vectors_final = vector_snapshot(engine)
    assert len(metadata_final) == 3
    assert sum(row["timestamp"] is None for row in metadata_final.values()) == 1
    for vid, value in vectors_after.items():
        assert vectors_final[vid] == value
    create_index(
        [
            "create-index",
            "--project-dir",
            str(engine),
            "--index-type",
            "IndexFlatIP",
            "--overwrite",
        ]
    )
    inspection = inspect_project(engine, ["keep", "image"])
    assert inspection["counts"]["keep"]["visual"] == 1
    assert inspection["counts"]["keep"]["audio"] == 1
    assert inspection["counts"]["image"]["visual"] == 1
    # Removing the last audio-bearing source removes its feature/index files,
    # while the whole-image vector stays NULL and searchable by upstream FAISS.
    prune_project(engine, ["keep", "image"], ["image"])
    create_index(
        [
            "create-index",
            "--project-dir",
            str(engine),
            "--index-type",
            "IndexFlatIP",
            "--overwrite",
        ]
    )
    assert inspect_project(engine, ["image"])["counts"]["image"]["visual"] == 1
    assert not list(engine.rglob("audio-*.faiss"))
    assert (engine / "unknown.note").read_text() == "must not be removed"
    project = open_project(engine)
    import numpy as np

    for models in project.load_search_indices().values():
        for index in models.values():
            _, found = index.index.search(np.array([[0, 0, 1]], dtype=np.float32), 10)
            real_ids = {int(value) for value in found[0] if value >= 0}
            final_meta, _ = vector_snapshot(engine)
            assert real_ids == set(final_meta)
            assert all(row["timestamp"] is None for row in final_meta.values())
    project.db_engine.dispose()
    project.thumbsdb_engine.dispose()
    assert bytes_digest(original) == original_hashes
    assert bytes_digest(delta) == delta_hashes


def exercise_real_merge_scope(tmp_path):
    from wise_bridge.feature_reuse import merge_delta, prune_project

    engine, delta = tmp_path / "engine", tmp_path / "delta"
    source = [("same", "image", [("image", "visual", None, [1, 0, 0])])]
    build_fixture(engine, source)
    build_fixture(delta, source)
    before = bytes_digest(engine)
    with unittest.TestCase().assertRaisesRegex(RuntimeError, "disjoint"):
        merge_delta(engine, delta, ["same"], ["same"])
    with unittest.TestCase().assertRaisesRegex(RuntimeError, "scope"):
        prune_project(engine, ["another-project"], ["another-project"])
    assert bytes_digest(engine) == before


def exercise_real_bbox_merge(tmp_path):
    import sqlalchemy as sa
    from wise._main.create_index import main as create_index
    from wise.feature.feature_extractor_factory import get_feature_extractor_class
    from wise.wise_project import WiseProject
    from wise_bridge import MODELS
    from wise_bridge.feature_reuse import merge_delta, prune_project
    from wise_bridge.upstream_compat import isolated_merge, open_project
    from wise_bridge.upstream_worker import inspect_project

    original, engine, delta = (
        tmp_path / "original",
        tmp_path / "new" / "engine",
        tmp_path / "new" / "delta",
    )
    build_fixture(
        original,
        [
            (
                "first",
                "image",
                [("image", "object", None, [1, 0, 0], [0.1, 0.2, 0.3, 0.4])],
            )
        ],
    )
    shutil.copytree(original, engine)
    before = bytes_digest(original)
    build_fixture(
        delta,
        [
            (
                "second",
                "video",
                [
                    ("video", "object", 1.5, [0, 1, 0], [0.2, 0.3, 0.1, 0.2]),
                    ("video", "object", 1.5, [0, 0, 1], [0.6, 0.5, 0.2, 0.3]),
                ],
            )
        ],
    )
    merge_delta(engine, delta, ["first"], ["second"])
    metadata, _ = vector_snapshot(engine)
    assert sorted(
        row["timestamp"] for row in metadata.values() if row["timestamp"] is not None
    ) == [1.5, 1.5]
    table = get_feature_extractor_class(MODELS["object"])._vector_metadata_table
    project = open_project(engine)
    with project.db_engine.connect() as conn:
        boxes = (
            conn.execute(sa.select(table).order_by(table.c.vector_id)).mappings().all()
        )
        assert len(boxes) == 3
        assert [[row["bbox_" + key] for key in "xywh"] for row in boxes] == [
            [0.1, 0.2, 0.3, 0.4],
            [0.2, 0.3, 0.1, 0.2],
            [0.6, 0.5, 0.2, 0.3],
        ]
        assert {row["vector_id"] for row in boxes} == set(metadata)
        # Test the real metadata-only getter with the same private filter binding.
        cls = isolated_merge(WiseProject._merge).__globals__[
            "get_feature_extractor_class"
        ](MODELS["object"])
        assert len(cls.get_vector_metadata(conn, list(metadata))) == 3
    # The same precise fix must be used by production search, not only merge or
    # a probe-only code path. Preserve caller ordering and both same-frame boxes.
    ids = list(reversed(metadata))
    production_boxes = project.get_vector_ext_metadata_for_ids(MODELS["object"], ids)
    assert [list(row.bbox) for row in production_boxes] == [
        [0.6, 0.5, 0.2, 0.3], [0.2, 0.3, 0.1, 0.2], [0.1, 0.2, 0.3, 0.4]
    ]
    project.db_engine.dispose()
    project.thumbsdb_engine.dispose()
    prune_project(engine, ["first", "second"], ["second"])
    create_index(
        [
            "create-index",
            "--project-dir",
            str(engine),
            "--index-type",
            "IndexFlatIP",
            "--overwrite",
        ]
    )
    assert inspect_project(engine, ["second"])["counts"]["second"]["object"] == 2
    project = open_project(engine)
    with project.db_engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 2
    project.db_engine.dispose()
    project.thumbsdb_engine.dispose()
    assert bytes_digest(original) == before


@unittest.skipUnless(
    os.environ.get("INSPIRATION_WISE_STORAGE_TESTS") == "1",
    "requires the pinned isolated WISE environment",
)
class RealWiseStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="wise-storage-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_image_audio_video_delete_and_merge(self):
        exercise_real_faiss_delete_and_image_delta(self.root)

    def test_cross_scope_and_duplicate_rejected(self):
        exercise_real_merge_scope(self.root)

    def test_bbox_vector_id_and_same_frame_metadata_survive_merge(self):
        exercise_real_bbox_merge(self.root)


if __name__ == "__main__":
    unittest.main()
