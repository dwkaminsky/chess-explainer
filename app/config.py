"""Validated deployment settings shared by the API and worker."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import ENGINE_CLEANUP_MARGIN


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
    search_time_seconds: float = Field(default=3.0, gt=0)
    hard_attempt_timeout_seconds: float = Field(default=10.0, gt=0)
    engine_threads: int = Field(default=1, ge=1)
    engine_hash_mb: int = Field(default=64, ge=1)
    candidate_target: int = Field(default=3, ge=1, le=3)
    max_continuation_plies: int = Field(default=6, ge=1, le=6)
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
        if self.hard_attempt_timeout_seconds - self.search_time_seconds < ENGINE_CLEANUP_MARGIN:
            raise ValueError(
                "HARD_ATTEMPT_TIMEOUT_SECONDS must exceed SEARCH_TIME_SECONDS by at least the cleanup margin"
            )
        if self.lease_seconds <= self.hard_attempt_timeout_seconds:
            raise ValueError(
                "LEASE_SECONDS must be greater than HARD_ATTEMPT_TIMEOUT_SECONDS"
            )
        if self.engine_threads != 1:
            raise ValueError("ENGINE_THREADS must be exactly 1")
        if self.engine_hash_mb != 64:
            raise ValueError("ENGINE_HASH_MB must be exactly 64")
        return self

    def evaluation_config(self) -> dict[str, int | float | str | None]:
        """Return the server-owned settings persisted on each task."""

        search_time = self.search_time_seconds
        hard_timeout = self.hard_attempt_timeout_seconds
        threads = self.engine_threads
        hash_mb = self.engine_hash_mb
        skill = 20
        return {
            "candidate_target": self.candidate_target,
            "max_continuation_plies": self.max_continuation_plies,
            "search_time_seconds": search_time,
            "search_time": search_time,
            "hard_attempt_timeout_seconds": hard_timeout,
            "hard_timeout": hard_timeout,
            "attempt_timeout": hard_timeout,
            "engine_threads": threads,
            "threads": threads,
            "engine_hash_mb": hash_mb,
            "hash_mb": hash_mb,
            "skill": skill,
            "engine_path": self.engine_path,
            "engine_version": self.engine_version,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get the process settings singleton (cached for dependency injection)."""

    return Settings()
