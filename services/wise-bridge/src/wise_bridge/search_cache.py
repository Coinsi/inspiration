"""Bounded caches inside the isolated WISE query process only."""

import gc
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectEntry:
    project: object
    service: object
    config: object
    mapping: dict
    index_bytes: int


class SearchCache:
    def __init__(self, max_models=1, max_projects=2, max_index_bytes=512 * 1024**2):
        self.max_models = max_models
        self.max_projects = max_projects
        self.max_index_bytes = max_index_bytes
        self.models = {}
        self.feature_fingerprints = {}
        self.projects = OrderedDict()

    @staticmethod
    def _load_project(path, index_bytes):
        from wise.api.services.project import LocalWiseProjectService
        from wise.config import APIConfig

        from wise_bridge.upstream_compat import open_project

        project = open_project(path, read_only=True)
        config = APIConfig(project_dir=path, command="search", index_type="IndexFlatIP")
        service = LocalWiseProjectService(project, config)
        mapping = {
            str(item.id): Path(item.path).stem for item in project.get_media_files()
        }
        return ProjectEntry(project, service, config, mapping, index_bytes)

    def _evict(self, key):
        entry = self.projects.pop(key)
        # WISE uses cached_property engines; avoid opening an unused thumbnail DB.
        for name in ("db_engine", "thumbsdb_engine"):
            engine = entry.project.__dict__.get(name)
            if engine is not None:
                engine.dispose()
        del entry
        gc.collect()

    def project(self, request):
        path = Path(request["project_dir"]).resolve()
        key = (request["project_id"], request["revision"], str(path))
        if key in self.projects:
            self.projects.move_to_end(key)
            return self.projects[key]
        # A revision is immutable. Never reuse the previous revision's indices
        # under a fresh key; drop same-project old snapshots before loading.
        for previous in list(self.projects):
            if previous[0] == key[0]:
                self._evict(previous)
        size = sum(file.stat().st_size for file in path.rglob("*.faiss"))
        if size > self.max_index_bytes:
            raise RuntimeError(
                "Project index exceeds the configured query cache budget"
            )
        while self.projects and (
            len(self.projects) >= self.max_projects
            or sum(entry.index_bytes for entry in self.projects.values()) + size
            > self.max_index_bytes
        ):
            self._evict(next(iter(self.projects)))
        entry = self._load_project(path, size)
        self.projects[key] = entry
        return entry

    def embedding(self, model, config, *, feature_fingerprint=None):
        from wise.api.services.embedding import EmbeddingService

        if (
            model in self.models
            and self.feature_fingerprints.get(model) != feature_fingerprint
        ):
            # Dropping Python references does not evict upstream's global model
            # cache. Fail so the supervisor can terminate the entire child.
            raise RuntimeError("Query feature fingerprint changed; restart required")
        if model not in self.models:
            if len(self.models) >= self.max_models:
                # The supervisor restarts before this boundary; do not evict by
                # del alone because upstream's functools.cache retains models.
                raise RuntimeError(
                    "Query model cache budget exhausted; restart required"
                )
            self.models[model] = EmbeddingService.from_ids([model], config)
            self.feature_fingerprints[model] = feature_fingerprint
        return self.models[model]
