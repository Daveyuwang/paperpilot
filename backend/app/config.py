from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    environment: str = "development"
    secret_key: str = "change-me"
    cors_origins: str = "http://localhost:5173"

    # Database
    database_url: str = "postgresql+asyncpg://paperpilot:paperpilot@postgres:5432/paperpilot"
    database_url_sync: str = "postgresql://paperpilot:paperpilot@postgres:5432/paperpilot"

    # Redis
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # Qdrant
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_collection: str = "paperpilot_chunks"

    # LLM (server-default; per-guest settings may override via Redis)
    llm_protocol: str = "anthropic"  # openai_compatible | anthropic | gemini
    llm_base_url: str | None = None
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"

    # Backward-compatible Anthropic envs (deprecated)
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # Feature flags
    enable_intent_routing: bool = True
    web_search_enabled: bool = True
    inline_ingestion: bool = False

    # Deep Research execution checkpoints. Disabled non-production and test
    # environments use an in-memory saver. Production fails startup unless
    # encrypted Postgres persistence and its native schema are enabled.
    deep_research_checkpoint_enabled: bool = False
    deep_research_checkpoint_database_url: str | None = None
    deep_research_checkpoint_aes_key: str = ""
    deep_research_checkpoint_auto_setup: bool = False
    deep_research_checkpoint_pool_size: int = 4

    # External APIs
    semantic_scholar_api_key: str = ""
    tavily_api_key: str = ""

    # Models
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    embedding_dimension: int = 1024
    embedding_batch_size: int = 32
    embedding_fallback_enabled: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Langfuse observability
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    enable_tracing: bool = False

    # Upload
    upload_dir: str = "/app/uploads"
    max_upload_size_mb: int = 50
    max_upload_pages: int = 200
    max_concurrent_ingestion: int = 10
    page_parse_timeout: int = 30

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_async_database_url(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        if value.startswith("postgresql+asyncpg://"):
            return value
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("deep_research_checkpoint_pool_size")
    @classmethod
    def validate_deep_research_checkpoint_pool_size(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError(
                "deep_research_checkpoint_pool_size must be between 1 and 20"
            )
        return value

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
