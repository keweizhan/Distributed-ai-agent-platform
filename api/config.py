from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str

    openai_api_key: str = "sk-not-set"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # CORS — comma-separated list of allowed origins.
    # Override via CORS_ORIGINS env var in production.
    cors_origins: str = (
        "http://localhost:3001,"
        "http://127.0.0.1:3001,"
        "http://119.28.233.128:3002"
    )

    # JWT — set a strong random secret in production
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()  # type: ignore[call-arg]
