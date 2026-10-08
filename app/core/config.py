"""Application settings, loaded from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Bulk Certificate Generator"
    database_url: str = f"sqlite:///{BASE_DIR / 'storage' / 'app.db'}"
    storage_dir: Path = BASE_DIR / "storage" / "certificates"
    organization_name: str = "Example Learning Academy"
    max_recipients_per_job: int = Field(default=1000, ge=1)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
