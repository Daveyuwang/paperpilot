from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    environment: str = "development"
    secret_key: str = "change-me"
    cors_origins: str = "http://localhost:5173"

    # Database
    database_url: str = (
        "postgresql+asyncpg://paperpilot:paperpilot@postgres:5432/paperpilot"
    )
    database_url_sync: str = (
        "postgresql://paperpilot:paperpilot@postgres:5432/paperpilot"
    )

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

    # Agent skills (third-party SKILL.md files are advisory prompt context only)
    agent_skills_enabled: bool = True
    agent_skills_repo_url: str = (
        "https://github.com/Orchestra-Research/AI-research-SKILLs.git"
    )
    agent_skills_repo_ref: str = "main"
    agent_skills_cache_dir: str = ".runtime/skills"
    agent_skills_refresh_seconds: int = 86400
    agent_skills_clone_timeout_seconds: int = 90
    agent_skills_max_count: int = 200
    agent_skills_max_file_bytes: int = 200_000
    agent_skills_max_selected: int = 2
    agent_skills_max_prompt_chars: int = 12_000
    agent_skills_min_score: float = 6.0
    agent_skills_cache_max_entries: int = 16
    agent_skills_cache_max_bytes: int = 2_000_000
    agent_skills_max_reference_bytes: int = 200_000
    agent_skills_blocked_names: str = "autoresearch"

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

    @model_validator(mode="after")
    def validate_agent_skill_settings(self):
        """Validate loader bounds only when the feature is enabled."""

        if not self.agent_skills_enabled:
            return self
        positive_fields = {
            "agent_skills_refresh_seconds": self.agent_skills_refresh_seconds,
            "agent_skills_clone_timeout_seconds": self.agent_skills_clone_timeout_seconds,
            "agent_skills_max_count": self.agent_skills_max_count,
            "agent_skills_max_file_bytes": self.agent_skills_max_file_bytes,
            "agent_skills_max_selected": self.agent_skills_max_selected,
            "agent_skills_cache_max_entries": self.agent_skills_cache_max_entries,
            "agent_skills_cache_max_bytes": self.agent_skills_cache_max_bytes,
            "agent_skills_max_reference_bytes": self.agent_skills_max_reference_bytes,
        }
        invalid = [name for name, value in positive_fields.items() if value <= 0]
        if invalid:
            raise ValueError(
                f"agent skill settings must be positive: {', '.join(invalid)}"
            )
        if self.agent_skills_max_prompt_chars < 1_024:
            raise ValueError("agent_skills_max_prompt_chars must be at least 1024")
        if self.agent_skills_min_score < 0:
            raise ValueError("agent_skills_min_score cannot be negative")
        upper_bounds = {
            "agent_skills_max_count": (self.agent_skills_max_count, 2_048),
            "agent_skills_max_file_bytes": (
                self.agent_skills_max_file_bytes,
                2 * 1024 * 1024,
            ),
            "agent_skills_max_selected": (self.agent_skills_max_selected, 8),
            "agent_skills_max_prompt_chars": (
                self.agent_skills_max_prompt_chars,
                128_000,
            ),
            "agent_skills_cache_max_entries": (
                self.agent_skills_cache_max_entries,
                512,
            ),
            "agent_skills_cache_max_bytes": (
                self.agent_skills_cache_max_bytes,
                64 * 1024 * 1024,
            ),
            "agent_skills_max_reference_bytes": (
                self.agent_skills_max_reference_bytes,
                2 * 1024 * 1024,
            ),
        }
        oversized = [
            name for name, (value, maximum) in upper_bounds.items() if value > maximum
        ]
        if oversized:
            raise ValueError(
                f"agent skill settings exceed safety bounds: {', '.join(oversized)}"
            )
        if self.agent_skills_cache_max_bytes < 1_024:
            raise ValueError("agent_skills_cache_max_bytes must be at least 1024")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def agent_skills_blocked_names_list(self) -> list[str]:
        return [
            name.strip()
            for name in self.agent_skills_blocked_names.split(",")
            if name.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
