"""Reviewed compatibility corrections for the locked WISE revision.

The pinned load_search_indices builds a ModalityType-keyed dict, but reads its
audio-only branch with MediaType.AUDIO. Only that function's global enum binding
is corrected; the repository, feature encoders and search algorithms are unchanged.
Any new source structure/revision is rejected and needs an explicit review.
"""

import functools
import hashlib
import inspect
import textwrap
import types

from wise_bridge import WISE_COMMIT

PATCH_COMMIT = "fcfa443fbb46eb361bb19151339338616838a5b5"
LOADER_SHA256 = "25df41f17ea2009162936238712075059eb57251e7fd59290b370a4166f7d370"
MERGE_SHA256 = "2c3e58a7dcbdd3a3b4535d1c361a8e5661ec3373152bc4c3058da70086be4220"
FILTER_SHA256 = "ecc00f72a83c660e4927c7fdb3a3da8b1a07c2e6d3e19833b2cbc45738e1e634"
METADATA_SHA256 = "2070427b28ce3f2e7c1f983bf8d234a7ad00f987cdf9e2be8adedb8f159c49a7"


def executable_filter(original):
    """Pass Select to SQLAlchemy 2, preserving typed bound values and ordering."""
    source = inspect.getsource(original)
    statement = "compiled_stmt = sql_stmt.compile(conn)"
    if (
        WISE_COMMIT != PATCH_COMMIT
        or hashlib.sha256(source.encode()).hexdigest() != FILTER_SHA256
        or source.count(statement) != 1
    ):
        raise RuntimeError(
            "WISE SQL filter compatibility structure changed; review required"
        )
    namespace = original.__globals__.copy()
    exec(  # noqa: S102 - exact commit and full reviewed function hash checked above
        compile(
            source.replace(statement, "compiled_stmt = sql_stmt"),
            "<wise-executable-filter>",
            "exec",
        ),
        namespace,
    )
    return functools.update_wrapper(namespace[original.__name__], original)


def isolated_merge(original):
    """Apply both corrections only to this one private merge invocation.

    Optional bbox metadata getters import the same SQL filter by name. Give
    their metadata-only class a private function namespace as well; no model
    instance, global class or upstream module is modified.
    """
    corrected = image_safe_merge(original)
    database = original.__globals__["wise_db"]
    filter_fn = executable_filter(database.prepare_filter_stmt)
    corrected.__globals__["wise_db"] = types.SimpleNamespace(
        **{**vars(database), "prepare_filter_stmt": filter_fn}
    )
    corrected.__globals__["get_feature_extractor_class"] = metadata_lookup(
        original.__globals__["get_feature_extractor_class"], database, filter_fn
    )
    return corrected


def metadata_lookup(lookup, database, filter_fn):
    """Private metadata-only subclasses, shared by merge and production query."""
    classes = {}

    def metadata_class(feature_id):
        if feature_id in classes:
            return classes[feature_id]
        cls = lookup(feature_id)
        if getattr(cls, "_vector_metadata_table", None) is not None:
            query = inspect.unwrap(cls.vector_metadata_query)
            if (
                query.__globals__.get("prepare_filter_stmt")
                is not database.prepare_filter_stmt
            ):
                raise RuntimeError(
                    "WISE metadata filter structure changed; review required"
                )
            method = types.FunctionType(
                query.__code__,
                {**query.__globals__, "prepare_filter_stmt": filter_fn},
                query.__name__,
                query.__defaults__,
                query.__closure__,
            )
            cls = type(
                cls.__name__ + "MergeMetadata",
                (cls,),
                {"vector_metadata_query": classmethod(method)},
            )
        classes[feature_id] = cls
        return cls

    return metadata_class


def metadata_safe_getter(original):
    from wise import db

    if (
        WISE_COMMIT != PATCH_COMMIT
        or hashlib.sha256(inspect.getsource(original).encode()).hexdigest()
        != METADATA_SHA256
    ):
        raise RuntimeError("WISE metadata getter structure changed; review required")
    namespace = original.__globals__.copy()
    namespace["get_feature_extractor_class"] = metadata_lookup(
        namespace["get_feature_extractor_class"],
        db,
        executable_filter(db.prepare_filter_stmt),
    )
    corrected = types.FunctionType(
        original.__code__,
        namespace,
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    return functools.update_wrapper(corrected, original)


def image_safe_merge(original):
    """Keep whole-image timestamps NULL in the pinned upstream project merge.

    WISE compares every timestamp to the destination's last timestamp, including
    the None used by image vectors. The bridge only merges disjoint media sets;
    images must be copied without inventing a timestamp. No encoder is changed.
    """
    source = inspect.getsource(original)
    condition = 'if vector["timestamp"] <= min_ts:'
    if (
        WISE_COMMIT != PATCH_COMMIT
        or hashlib.sha256(source.encode()).hexdigest() != MERGE_SHA256
        or source.count(condition) != 1
    ):
        raise RuntimeError(
            "WISE image-merge compatibility structure changed; review required"
        )
    corrected_source = source.replace(
        condition,
        'if vector["timestamp"] is not None and min_ts is not None and vector["timestamp"] <= min_ts:',
    )
    namespace = original.__globals__.copy()
    exec(  # noqa: S102 - exact commit and full reviewed function hash checked above
        compile(textwrap.dedent(corrected_source), "<wise-image-merge>", "exec"),
        namespace,
    )
    return functools.update_wrapper(namespace[original.__name__], original)


def audio_safe_loader(original):
    source = inspect.getsource(original)
    if (
        WISE_COMMIT != PATCH_COMMIT
        or hashlib.sha256(source.encode()).hexdigest() != LOADER_SHA256
        or source.count("MediaType") != 1
        or source.count("search_indices[MediaType.AUDIO]") != 1
        or original.__globals__.get("MediaType") is None
        or original.__globals__.get("ModalityType") is None
    ):
        raise RuntimeError(
            "WISE audio-index compatibility structure changed; review required"
        )
    corrected = types.FunctionType(
        original.__code__,
        {**original.__globals__, "MediaType": original.__globals__["ModalityType"]},
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    corrected.__kwdefaults__ = original.__kwdefaults__
    return functools.update_wrapper(corrected, original)


def open_project(path, **kwargs):
    from wise.wise_project import WiseProject

    project = WiseProject(path, **kwargs)
    project.load_search_indices = types.MethodType(
        audio_safe_loader(WiseProject.load_search_indices), project
    )
    project.get_vector_ext_metadata_for_ids = types.MethodType(
        metadata_safe_getter(WiseProject.get_vector_ext_metadata_for_ids), project
    )
    return project
