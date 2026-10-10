import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("WISE_BRIDGE_DATA", "./wise-data")
        ).resolve()
    )
    token: str = field(default_factory=lambda: os.getenv("WISE_BRIDGE_TOKEN", ""))
    python: str = field(
        default_factory=lambda: os.getenv("WISE_PYTHON", sys.executable)
    )
    max_file_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WISE_BRIDGE_MAX_FILE_BYTES", str(8 * 1024**3))
        )
    )
    max_storage_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WISE_BRIDGE_MAX_STORAGE_BYTES", str(100 * 1024**3))
        )
    )
    min_free_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WISE_BRIDGE_MIN_FREE_BYTES", str(2 * 1024**3))
        )
    )
    max_pending_jobs: int = 1000
    max_query_bytes: int = 20 * 1024**2
    process_timeout_seconds: int = 24 * 3600
    search_timeout_seconds: int = 300
    max_engine_log_bytes: int = 32 * 1024**2
    query_cache_models: int = field(
        default_factory=lambda: int(os.getenv("WISE_QUERY_CACHE_MODELS", "1"))
    )
    query_cache_projects: int = field(
        default_factory=lambda: int(os.getenv("WISE_QUERY_CACHE_PROJECTS", "2"))
    )
    query_cache_index_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WISE_QUERY_CACHE_INDEX_BYTES", str(512 * 1024**2))
        )
    )
    enabled_capabilities: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            os.getenv("WISE_BRIDGE_CAPABILITIES", "visual,audio").split(",")
        )
    )
    worker_enabled: bool = True
    optional_manifest_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("WISE_OPTIONAL_MANIFEST_DIR", "/data/optional-models")
        ).resolve()
    )
    optional_use: str = field(
        default_factory=lambda: os.getenv("WISE_OPTIONAL_USE", "unspecified")
    )

    def validate(self):
        if len(self.token) < 24:
            raise ValueError("WISE_BRIDGE_TOKEN must contain at least 24 characters")
        if not set(self.enabled_capabilities) <= {"visual", "audio", "face", "object"}:
            raise ValueError("Unknown WISE capability")
        if self.max_file_bytes <= 0 or self.max_storage_bytes <= 0:
            raise ValueError("Storage limits must be positive")
        if not 1 <= self.query_cache_models <= 4:
            raise ValueError("Query model cache must contain 1 to 4 models")
        if (
            not 1 <= self.query_cache_projects <= 16
            or self.query_cache_index_bytes <= 0
        ):
            raise ValueError("Query index cache limits must be positive and bounded")
