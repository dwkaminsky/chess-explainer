"""Worker lifecycle tests using an in-memory task API and evaluator doubles."""

import threading
from types import SimpleNamespace

import pytest

from app.stockfish import EngineTimeout
from app.worker import (
    Worker,
    WorkerConfig,
    WorkerConfigError,
    coerce_config,
    validate_startup_config,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class FakeSession:
    def close(self):
        pass


class FakeTasks:
    def __init__(self, task):
        self.task = task
        self.recovered = 0
        self.claimed = 0
        self.completed = []
        self.retried = []
        self.failed = []

    def recover_expired_tasks(self, session):
        self.recovered += 1

    def claim_next_task(self, session):
        self.claimed += 1
        task, self.task = self.task, None
        return task

    def complete_task(self, session, task_id, lease_token, result, factual_result=None, **kwargs):
        self.completed.append((task_id, lease_token, result, factual_result))
        return True

    def retry_task(self, session, task_id, lease_token, error_code, error_message, **kwargs):
        self.retried.append((task_id, error_code, error_message))

    def fail_task(self, session, task_id, lease_token, error_code, error_message, **kwargs):
        self.failed.append((task_id, error_code))


def make_worker(tasks, evaluator):
    return Worker(
        session_factory=FakeSession,
        task_api=tasks,
        evaluator=evaluator,
        evaluation_config={"engine_path": "unused"},
        stop_event=threading.Event(),
    )


def test_worker_claims_evaluates_and_completes():
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    worker = make_worker(tasks, lambda fen, config: 34)
    assert worker.process_once() is True
    assert tasks.recovered == 1
    assert tasks.completed and tasks.completed[0][0:3] == ("t1", "l1", 34)
    assert tasks.completed[0][3]["version"] == 1
    assert tasks.completed[0][3]["facts"]["material"]["white"]["pawn"] == 8


def test_worker_retries_engine_failure_then_fails_on_second_attempt():
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    worker = make_worker(tasks, lambda fen, config: (_ for _ in ()).throw(EngineTimeout("slow")))
    assert worker.process_once() is True
    assert tasks.retried == [("t1", "ENGINE_TIMEOUT", "The position could not be evaluated within the allowed time.")]

    tasks.task = SimpleNamespace(id="t1", fen=START, attempts=2, lease_token="l2")
    assert worker.process_once() is True
    assert tasks.failed == [("t1", "ENGINE_TIMEOUT")]


def test_startup_rejects_timeout_longer_than_lease():
    try:
        validate_startup_config(WorkerConfig(hard_timeout=30, lease_seconds=30), check_engine=False)
    except WorkerConfigError:
        pass
    else:
        raise AssertionError("invalid lease relationship accepted")


def test_config_accepts_settings_snapshot_field_names():
    config = coerce_config(SimpleNamespace(
        engine_path="sf",
        search_time_seconds=2.0,
        hard_attempt_timeout_seconds=9.0,
        engine_threads=1,
        engine_hash_mb=64,
        poll_interval_seconds=0.5,
        lease_seconds=30.0,
    ))
    assert config.search_time == 2.0
    assert config.hard_timeout == 9.0
    assert config.poll_interval == 0.5


def test_success_persistence_error_does_not_become_engine_retry():
    class BrokenTasks(FakeTasks):
        def complete_task(self, session, task_id, lease_token, result, factual_result=None, **kwargs):
            raise RuntimeError("database is down")

    tasks = BrokenTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    worker = make_worker(tasks, lambda fen, config: 34)
    with pytest.raises(RuntimeError, match="database is down"):
        worker.process_once()
    assert tasks.retried == []
    assert worker._db_blocked is True


def test_factual_generation_failure_is_terminal():
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    worker = make_worker(tasks, lambda fen, config: 34)

    from app import worker as worker_module

    original = worker_module.build_factual_result

    try:
        worker_module.build_factual_result = lambda board: (_ for _ in ()).throw(
            worker_module.FactExtractionError("no facts")
        )
        assert worker.process_once() is True
    finally:
        worker_module.build_factual_result = original

    assert tasks.retried == []
    assert tasks.failed == [("t1", "FACT_EXTRACTION_FAILED")]
