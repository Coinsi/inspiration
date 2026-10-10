"""Copy/filter real WISE features inside an unpublished bridge snapshot only.

The pinned WISE FaissStore and project merge perform the storage operations. This
module never substitutes an encoder or a search/index implementation. It runs in
the isolated upstream environment, under the normal bounded child-process lock.
"""

import shutil
import types
import uuid
from pathlib import Path


def _inside(root, path):
    root, path = Path(root).absolute(), Path(path).absolute()
    if (
        not path.is_relative_to(root)
        or path.resolve() != path
        or root.resolve() != root
    ):
        raise RuntimeError("Feature snapshot path failed validation")
    return path


def source_map(project, expected):
    rows = list(project.get_media_files())
    mapping = {item.id: Path(item.path).stem for item in rows}
    if len(mapping) != len(expected) or set(mapping.values()) != set(expected):
        raise RuntimeError("Feature snapshot source scope does not match its manifest")
    return mapping


def _filter_store(project, root, modality, model, asset, retained_media):
    import sqlalchemy as sa
    from wise import db
    from wise.feature.store.faiss_store import FaissStore

    directory = _inside(root, asset["features_dir"])
    scratch = directory.parent / (".retained-" + uuid.uuid4().hex)
    scratch.mkdir()
    reader, writer = FaissStore(modality, directory), FaissStore(modality, scratch)
    writer.enable_write(shard_maxcount=4096)
    seen = set()
    table = db.vectors_table
    try:
        with project.db_engine.connect() as conn:
            expected_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(table)
                .where(
                    table.c.modality == modality, table.c.feature_extractor_id == model
                )
            ).scalar_one()
            for ids, vectors in reader.iter_batch(512):
                values = ids.tolist()
                if len(set(values)) != len(values) or seen.intersection(values):
                    raise RuntimeError("Duplicate feature IDs in snapshot")
                seen.update(values)
                rows = dict(
                    conn.execute(
                        sa.select(table.c.id, table.c.media_id).where(
                            table.c.id.in_(values),
                            table.c.modality == modality,
                            table.c.feature_extractor_id == model,
                        )
                    ).all()
                )
                if len(rows) != len(values):
                    raise RuntimeError("Feature storage and metadata IDs disagree")
                positions = [
                    index
                    for index, value in enumerate(values)
                    if rows[value] in retained_media
                ]
                if positions:
                    writer.add(
                        [values[index] for index in positions], vectors[positions]
                    )
            if len(seen) != expected_count:
                raise RuntimeError("Feature storage and metadata counts disagree")
        writer.close()
        reader.close()
        # All paths below belong to this freshly copied, unpublished project.
        # Only the known shard/index files are replaced, never glob-deleted.
        for filename in reader.filenames:
            _inside(root, filename).unlink()
        for filename in asset["index_files"]:
            _inside(root, Path(asset["index_dir"]) / filename).unlink()
        for filename in writer.filenames:
            path = _inside(root, filename)
            path.replace(directory / path.name)
    finally:
        reader.close()
        writer.close()
        shutil.rmtree(_inside(root, scratch))


def prune_project(project_dir, expected_sources, retained_sources):
    import sqlalchemy as sa
    from wise import db

    from wise_bridge.upstream_compat import open_project

    root = _inside(project_dir, project_dir)
    if not retained_sources or not set(retained_sources) <= set(expected_sources):
        raise RuntimeError("Invalid retained feature source scope")
    project = open_project(root)
    try:
        mapping = source_map(project, expected_sources)
        retained = {mid for mid, sid in mapping.items() if sid in retained_sources}
        removed = set(mapping) - retained
        if not removed:
            return {"retained_sources": len(retained), "removed_sources": 0}
        for modality, models in project.discover_assets().items():
            for model, asset in models.items():
                _filter_store(project, root, modality, model, asset, retained)
        # Database foreign-key cascades remove vectors, shot metadata and bbox
        # metadata together. Thumbnails live in a separate database, so remove
        # their corresponding media IDs explicitly before publication.
        with project.db_engine.begin() as conn:
            if conn.exec_driver_sql("PRAGMA foreign_keys").scalar() != 1:
                raise RuntimeError("WISE feature deletion requires foreign keys")
            for start in range(0, len(removed), 512):
                conn.execute(
                    sa.delete(db.media_table).where(
                        db.media_table.c.id.in_(sorted(removed)[start : start + 512])
                    )
                )
            conn.execute(
                sa.delete(db.source_collections_table).where(
                    db.source_collections_table.c.id.not_in(
                        sa.select(db.media_table.c.source_collection_id)
                    )
                )
            )
            if conn.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise RuntimeError("WISE snapshot metadata is inconsistent")
        with project.thumbsdb_engine.begin() as conn:
            conn.execute(
                sa.delete(db.thumbnails_table).where(
                    db.thumbnails_table.c.media_id.not_in(sorted(retained))
                )
            )
        source_map(project, retained_sources)
        return {"retained_sources": len(retained), "removed_sources": len(removed)}
    finally:
        project.db_engine.dispose()
        project.thumbsdb_engine.dispose()


def merge_delta(project_dir, delta_dir, retained_sources, delta_sources):
    from wise.wise_project import WiseProject

    from wise_bridge.upstream_compat import isolated_merge, open_project

    root = _inside(project_dir, project_dir)
    delta = _inside(root.parent, delta_dir)
    if delta == root or root.is_relative_to(delta) or delta.is_relative_to(root):
        raise RuntimeError("Feature delta must be isolated from the destination")
    if not delta_sources or set(retained_sources) & set(delta_sources):
        raise RuntimeError("Feature merge requires disjoint source sets")
    project, incoming = open_project(root), open_project(delta, read_only=True)
    try:
        source_map(project, retained_sources)
        source_map(incoming, delta_sources)
        project._merge = types.MethodType(isolated_merge(WiseProject._merge), project)
        # The upstream public merge catches exceptions and runs an in-place
        # rollback with glob cleanup. Our isolation is stronger: propagate any
        # error and discard the owned unpublished snapshot as a whole instead.
        project._merge(incoming, dry_run=False)
        source_map(project, [*retained_sources, *delta_sources])
        return {"merged_sources": len(delta_sources)}
    finally:
        for item in (project, incoming):
            item.db_engine.dispose()
            item.thumbsdb_engine.dispose()
