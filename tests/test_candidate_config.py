from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_candidate_settings_production_snapshot_and_evaluation() -> None:
    settings = Settings(
        _env_file=None,
        search_time_seconds=3.0,
        hard_attempt_timeout_seconds=10.0,
        engine_path="stockfish",
        engine_version=None,
        engine_threads=1,
        engine_hash_mb=64,
        candidate_target=3,
        max_continuation_plies=6,
        lease_seconds=30.0,
    )

    assert settings.search_time_seconds == 3.0
    assert settings.hard_attempt_timeout_seconds == 10.0
    assert settings.engine_threads == 1
    assert settings.engine_hash_mb == 64
    assert settings.candidate_target == 3
    assert settings.max_continuation_plies == 6
    assert settings.lease_seconds == 30.0

    assert settings.evaluation_config() == {
        "candidate_target": 3,
        "max_continuation_plies": 6,
        "search_time_seconds": 3.0,
        "search_time": 3.0,
        "hard_attempt_timeout_seconds": 10.0,
        "hard_timeout": 10.0,
        "attempt_timeout": 10.0,
        "engine_threads": 1,
        "threads": 1,
        "engine_hash_mb": 64,
        "hash_mb": 64,
        "skill": 20,
        "engine_path": "stockfish",
        "engine_version": None,
    }


@pytest.mark.parametrize("field_name,bad_value", [("candidate_target", 0), ("candidate_target", 4)])
def test_candidate_target_bounds(field_name: str, bad_value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: bad_value})


@pytest.mark.parametrize(
    "field_name,bad_value",
    [("max_continuation_plies", 0), ("max_continuation_plies", 7)],
)
def test_max_continuation_plies_bounds(field_name: str, bad_value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: bad_value})


def test_lease_must_stay_above_hard_attempt_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            lease_seconds=10.0,
            hard_attempt_timeout_seconds=10.0,
        )


def test_search_time_cannot_exceed_hard_attempt_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            search_time_seconds=10.1,
            hard_attempt_timeout_seconds=10.0,
        )


def test_search_time_must_leave_cleanup_margin_before_hard_attempt_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            search_time_seconds=10.0,
            hard_attempt_timeout_seconds=10.0,
        )


def test_search_time_and_hard_timeout_need_at_least_cleanup_margin() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            search_time_seconds=1.0,
            hard_attempt_timeout_seconds=1.49,
        )


@pytest.mark.parametrize(
    "field_name,bad_value",
    [("engine_threads", 0), ("engine_threads", 2), ("engine_hash_mb", 63)],
)
def test_fixed_engine_resource_contract(field_name: str, bad_value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: bad_value})
