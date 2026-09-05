"""Validated deployment settings shared by the API and worker."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings.

    Both processes use the same settings model.  The engine settings are
    included in each task's ``evaluation_config`` snapshot by the API/worker,
    making a result reproducible even after a deployment changes defaults.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "postgresql+psycopg://chess:chess@db:5432/chess"
    # A separate direct URL is useful for migration tooling; runtime code uses
    # database_url.  It is deliberately optional to keep one canonical setting.
    database_url_direct: str | None = None

    engine_path: str = "stockfish"
    engine_version: str | None = None
    search_time_seconds: float = Field(default=1.0, gt=0)
    hard_attempt_timeout_seconds: float = Field(default=10.0, gt=0)
    engine_threads: int = Field(default=1, ge=1)
    engine_hash_mb: int = Field(default=64, ge=1)
    lease_seconds: float = Field(default=30.0, gt=0)
    poll_interval_seconds: float = Field(default=0.5, gt=0)
    max_request_body_bytes: int = Field(default=4096, ge=1)
    sql_echo: bool = False

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("DATABASE_URL must not be empty")
        return value

    @model_validator(mode="after")
    def validate_lease_budget(self) -> "Settings":
        if self.lease_seconds <= self.hard_attempt_timeout_seconds:
            raise ValueError(
                "LEASE_SECONDS must be greater than HARD_ATTEMPT_TIMEOUT_SECONDS"
            )
        return self

    def evaluation_config(self) -> dict[str, int | float | str | None]:
        """Return the server-owned settings persisted on each task."""

        return {
            "search_time_seconds": self.search_time_seconds,
            "hard_attempt_timeout_seconds": self.hard_attempt_timeout_seconds,
            "engine_threads": self.engine_threads,
            "engine_hash_mb": self.engine_hash_mb,
            "engine_path": self.engine_path,
            "engine_version": self.engine_version,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get the process settings singleton (cached for dependency injection)."""

    return Settings()
