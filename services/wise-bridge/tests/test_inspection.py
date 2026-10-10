"""Structural inspection checks; these tests do not run WISE inference."""

import sys
from types import ModuleType, SimpleNamespace

import pytest
import sqlalchemy as sa

from wise_bridge import MODELS
from wise_bridge.upstream_worker import inspect_project


def project_stub(monkeypatch, rows, indices):
    metadata = sa.MetaData()
    table = sa.Table(
        "vectors",
        metadata,
        sa.Column("media_id", sa.Integer),
        sa.Column("modality", sa.String),
        sa.Column("feature_extractor_id", sa.String),
    )
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(table.insert(), rows)
    project = SimpleNamespace(
        db_engine=engine,
        get_media_files=lambda: [SimpleNamespace(id=1, path="/sources/first.mp4")],
        load_search_indices=lambda: indices,
    )
    wise = ModuleType("wise")
    wise.db = SimpleNamespace(vectors_table=table)
    module = ModuleType("wise.wise_project")
    module.WiseProject = lambda directory: project
    monkeypatch.setitem(sys.modules, "wise", wise)
    monkeypatch.setitem(sys.modules, "wise.wise_project", module)
    monkeypatch.setattr(
        "wise_bridge.upstream_compat.open_project", lambda path: project
    )
    return engine


def test_features_without_loaded_index_are_not_ready(monkeypatch):
    engine = project_stub(
        monkeypatch,
        [
            {
                "media_id": 1,
                "modality": "video",
                "feature_extractor_id": MODELS["visual"],
            },
        ],
        {"video": {}},
    )
    try:
        with pytest.raises(RuntimeError, match="no searchable index"):
            inspect_project("unused", ["first"])
    finally:
        engine.dispose()


def test_loaded_index_count_must_match_feature_rows(monkeypatch):
    engine = project_stub(
        monkeypatch,
        [
            {
                "media_id": 1,
                "modality": "video",
                "feature_extractor_id": MODELS["visual"],
            },
        ],
        {"video": {MODELS["visual"]: SimpleNamespace(index=SimpleNamespace(ntotal=0))}},
    )
    try:
        with pytest.raises(RuntimeError, match="counts disagree"):
            inspect_project("unused", ["first"])
    finally:
        engine.dispose()


def test_capability_counts_only_loaded_matching_indices(monkeypatch):
    engine = project_stub(
        monkeypatch,
        [
            {
                "media_id": 1,
                "modality": "video",
                "feature_extractor_id": MODELS["visual"],
            },
            {
                "media_id": 1,
                "modality": "audio",
                "feature_extractor_id": MODELS["audio"],
            },
        ],
        {
            "video": {
                MODELS["visual"]: SimpleNamespace(index=SimpleNamespace(ntotal=1))
            },
            "audio": {
                MODELS["audio"]: SimpleNamespace(index=SimpleNamespace(ntotal=1))
            },
        },
    )
    try:
        result = inspect_project("unused", ["first"])
        assert result["counts"]["first"] == {
            "visual": 1,
            "audio": 1,
            "face": 0,
            "object": 0,
        }
    finally:
        engine.dispose()
