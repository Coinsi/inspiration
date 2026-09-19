"""应用配置:从环境变量 / .env 读取。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_name: str = "Inspiration"
    app_env: str = "dev"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 720
    jwt_algorithm: str = "HS256"

    # 数据库
    database_url: str = "postgresql+psycopg://inspiration:inspiration@localhost:5432/inspiration"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_eager: bool = False  # true: 任务同步执行(无需 worker/broker,便于本地验收)
    generation_executor: str = "celery"  # local: 单进程后台队列；celery: 持久化分布式队列

    # 对象存储:minio(生产)| fs(本地文件系统,轻量自托管/开发)
    storage_backend: str = "minio"
    fs_storage_dir: str = "./_storage"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "inspiration"
    minio_secure: bool = False
    library_staging_dir: str = "./_library_staging"
    library_max_upload_bytes: int = 10 * 1024 * 1024 * 1024
    agent_worker_enabled: bool = True
    canvas_worker_enabled: bool = True
    library_worker_enabled: bool = True
    library_indexer_url: str = "http://127.0.0.1:8011"
    library_indexer_token: str | None = None
    transcription_worker_enabled: bool = True
    transcriber_url: str = ""
    transcriber_token: str | None = None

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
