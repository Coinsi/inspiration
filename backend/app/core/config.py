"""应用配置:从环境变量 / .env 读取。"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_name: str = "Inspiration"
    app_env: str = "dev"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 720
    jwt_algorithm: str = "HS256"

    # Google OIDC: only a complete, valid configuration exposes the provider.
    google_client_id: str = ""
    google_client_secret: str = Field(default="", repr=False)
    google_redirect_uri: str = ""
    google_frontend_url: str = ""
    google_registration_enabled: bool = False

    # 数据库
    database_url: str = "postgresql+psycopg://inspiration:inspiration@localhost:5432/inspiration"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_eager: bool = False  # true: 任务同步执行(无需 worker/broker,便于本地验收)
    generation_executor: str = "celery"  # local: 单进程后台队列；celery: 持久化分布式队列

    # 对象存储:minio(生产)| fs(本地文件系统,轻量自托管/开发)
    storage_backend: str = "minio"
    storage_gc_enabled: bool = False
    archive_staging_quota_bytes: int = Field(default=200 * 1024**3, ge=0)
    archive_project_quota_bytes: int = Field(default=50 * 1024**3, ge=0)
    archive_disk_headroom_bytes: int = Field(default=512 * 1024**2, ge=0)
    archive_retention_days: int = Field(default=7, ge=1)
    fs_storage_dir: str = "./_storage"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "inspiration"
    minio_secure: bool = False
    # Optional public HTTPS ingress to this API, independent of browser/worker origin.
    generation_reference_public_base_url: str = ""
    library_staging_dir: str = "./_library_staging"
    library_max_upload_bytes: int = 10 * 1024 * 1024 * 1024
    library_staging_max_bytes: int = 100 * 1024 * 1024 * 1024
    library_staging_reserve_bytes: int = 2 * 1024 * 1024 * 1024
    attachment_cleanup_enabled: bool = True
    attachment_draft_ttl_days: int = Field(default=30, ge=1)
    agent_worker_enabled: bool = True
    production_harness_enabled: bool = True
    canvas_worker_enabled: bool = True
    library_worker_enabled: bool = True
    library_indexer_url: str = "http://127.0.0.1:8011"
    library_indexer_token: str | None = None
    # Optional upstream media services. Only the business API exposes library access.
    library_wise_url: str = ""
    library_wise_token: str | None = None
    library_wise_capabilities: list[str] = ["visual"]
    library_mediacms_url: str = ""
    library_mediacms_token: str | None = None
    library_mediacms_transcription_enabled: bool = False
    library_processing_enabled: bool = False
    library_processing_auto: bool = False
    library_processing_wise_auto_capabilities: list[str] = ["visual", "audio"]
    library_processing_poll_seconds: int = 10
    library_processing_temp_dir: str = "./_media_processing"
    library_processing_reserve_bytes: int = 2 * 1024 * 1024 * 1024
    library_processing_max_transfer_bytes: int = 50 * 1024 * 1024 * 1024
    transcription_worker_enabled: bool = True
    transcriber_url: str = ""
    transcriber_token: str | None = None
    # Harness must not assume an arbitrary ASR endpoint is free.
    harness_transcription_cost_mode: Literal["unknown", "self_hosted"] = "unknown"

    # 密钥加密
    credentials_fernet_key: str | None = None

    # 特性开关:pgvector(EXT-01 相似检索预留)。未安装扩展时置 false 自动降级,不建向量列。
    enable_pgvector: bool = True

    # AI 辅助拆解 LLM(决策 D2:云 API 为主)。默认 mock,配置 Key 后切 cloud_llm。
    decomposition_strategy: str = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
