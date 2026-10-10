"""Exercise the exact pinned upstream loader, with small index objects only."""

import logging
import os
import runpy
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest
from wise_bridge import upstream_compat


def pinned_loader(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "pinned_index_loader.txt"
    cls = runpy.run_path(str(fixture))["PinnedWiseProject"]
    original = cls.load_search_indices
    modalities = Enum(
        "ModalityType", {"AUDIO": "audio", "IMAGE": "image", "VIDEO": "video"}
    )
    media_types = Enum("MediaType", {"AUDIO": "audio"})
    index_file = tmp_path / "audio-IndexFlatIP.faiss"
    index_file.write_bytes(b"structural-test-index")
    original.__globals__.update(
        os=os,
        Path=Path,
        logger=logging.getLogger(__name__),
        ModalityType=modalities,
        MediaType=media_types,
        FeatureSearchIndex=lambda *args: SimpleNamespace(
            get_index_filename=lambda kind: str(index_file),
            load_index=lambda kind: True,
            index=SimpleNamespace(),
            is_internal_search_supported=True,
        ),
    )
    project = cls()
    project._search_indices = None
    project.enable_fts = lambda: False
    project.discover_assets = lambda: {
        modalities.AUDIO: {"clap": {"index_files": [str(index_file)]}}
    }
    return original, project, modalities, media_types


def test_exact_pinned_audio_loader_bug_is_corrected_without_global_mutation(tmp_path):
    original, project, modalities, media_types = pinned_loader(tmp_path)
    with pytest.raises(KeyError) as error:
        original(project)
    assert error.value.args[0] is media_types.AUDIO
    corrected = upstream_compat.audio_safe_loader(original)
    indices = corrected(project)
    assert set(indices) == {modalities.AUDIO}
    assert "clap" in indices[modalities.AUDIO]
    assert original.__globals__["MediaType"] is media_types
    assert project._search_indices is indices


def test_new_upstream_revision_requires_explicit_patch_review(tmp_path, monkeypatch):
    original, *_ = pinned_loader(tmp_path)
    monkeypatch.setattr(upstream_compat, "WISE_COMMIT", "new-revision")
    with pytest.raises(RuntimeError, match="review required"):
        upstream_compat.audio_safe_loader(original)


def test_changed_loader_structure_is_never_silently_patched(tmp_path, monkeypatch):
    original, *_ = pinned_loader(tmp_path)
    source = upstream_compat.inspect.getsource(original)
    monkeypatch.setattr(
        upstream_compat.inspect,
        "getsource",
        lambda fn: source.replace("MediaType.AUDIO", "ModalityType.AUDIO"),
    )
    with pytest.raises(RuntimeError, match="review required"):
        upstream_compat.audio_safe_loader(original)


def test_exact_pinned_merge_accepts_only_the_reviewed_source(monkeypatch):
    fixture = Path(__file__).parent / "fixtures" / "pinned_project_merge.txt"
    original = runpy.run_path(str(fixture))["PinnedWiseProject"]._merge
    corrected = upstream_compat.image_safe_merge(original)
    assert corrected.__code__ is not original.__code__
    assert corrected.__globals__ is not original.__globals__
    assert original.__code__.co_filename == str(fixture)
    source = upstream_compat.inspect.getsource(original)
    monkeypatch.setattr(
        upstream_compat.inspect,
        "getsource",
        lambda fn: source.replace("<= min_ts", "< min_ts"),
    )
    with pytest.raises(RuntimeError, match="review required"):
        upstream_compat.image_safe_merge(original)


def test_merge_patch_rejects_another_upstream_revision(monkeypatch):
    fixture = Path(__file__).parent / "fixtures" / "pinned_project_merge.txt"
    original = runpy.run_path(str(fixture))["PinnedWiseProject"]._merge
    monkeypatch.setattr(upstream_compat, "WISE_COMMIT", "unreviewed")
    with pytest.raises(RuntimeError, match="review required"):
        upstream_compat.image_safe_merge(original)


def test_exact_sql_filter_uses_executable_select_with_typed_ordered_sqlite_rows():
    import datetime

    import sqlalchemy as sa

    fixture = Path(__file__).parent / "fixtures" / "pinned_filter_stmt.txt"
    original = runpy.run_path(str(fixture))["prepare_filter_stmt"]
    corrected = upstream_compat.executable_filter(original)
    table = sa.Table(
        "items",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("when", sa.DateTime),
        sa.Column("data", sa.JSON),
    )
    engine = sa.create_engine("sqlite://")
    table.create(engine)
    date = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).replace(
        tzinfo=None
    )
    query = lambda cte: (
        sa.select(table)
        .select_from(cte.join(table, table.c.id == cte.c.id))
        .order_by(cte.c.rank)
    )
    with engine.begin() as conn:
        conn.execute(
            sa.insert(table),
            [
                {"id": 1, "when": date, "data": {"x": 0.2}},
                {"id": 2, "when": None, "data": {"x": 0.8}},
            ],
        )
        # Some host SQLAlchemy releases still accept Compiled; the pinned WISE
        # runtime does not. Verify the portable executable/typed result contract
        # here, and exercise the actual locked runtime in the storage suite.
        run = corrected(table, ["id"], query)
        rows = run(conn, [(2,), (1,)]).mappings().all()
        assert [row["id"] for row in rows] == [2, 1]
        assert rows[0]["when"] is None and rows[1]["when"] == date
        assert rows[0]["data"] == {"x": 0.8}
        assert run(conn, [(1,)]).mappings().one()["id"] == 1
    engine.dispose()


def test_sql_filter_new_structure_and_revision_are_not_patched(monkeypatch):
    fixture = Path(__file__).parent / "fixtures" / "pinned_filter_stmt.txt"
    original = runpy.run_path(str(fixture))["prepare_filter_stmt"]
    source = upstream_compat.inspect.getsource(original)
    with monkeypatch.context() as patch:
        patch.setattr(
            upstream_compat.inspect,
            "getsource",
            lambda fn: source.replace("compile(conn)", "compile()"),
        )
        with pytest.raises(RuntimeError, match="review required"):
            upstream_compat.executable_filter(original)
    monkeypatch.setattr(upstream_compat, "WISE_COMMIT", "unreviewed")
    with pytest.raises(RuntimeError, match="review required"):
        upstream_compat.executable_filter(original)
