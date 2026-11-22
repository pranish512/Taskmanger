"""Application settings, loaded from environment variables and an optional `.env` file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root (the folder that contains the `app` package).
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    app_name: str = "TaskMaster API"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["*"]

    # Security / JWT
    secret_key: str = "dev-only-secret-change-me-before-deploying-taskmaster-0123456789"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    bcrypt_rounds: int = 12  # cost factor; each +1 doubles hashing time

    # Storage: SQLite database file and local folder for attachments
    database_url: str = f"sqlite:///{(BASE_DIR / 'taskmaster.db').as_posix()}"
    upload_dir: Path = BASE_DIR / "uploads"
    max_upload_size_mb: int = 10

    # Generative AI (optional). Without a key, a built-in template is used instead.
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-5-5"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
