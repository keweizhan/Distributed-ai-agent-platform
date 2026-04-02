from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str

    openai_api_key: str = "sk-not-set"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    sandbox_backend: str = "subprocess"          # "subprocess" | "docker"
    sandbox_image: str = "python:3.11-slim"
    sandbox_timeout_seconds: int = 30
    worker_metrics_port: int = 9090

    # Memory layer (M7) — disabled by default
    memory_enabled: bool = False
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "agent_memory"
    embedding_model: str = "text-embedding-3-small"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()  # type: ignore[call-arg]
