"""Worker lifecycle tests using an in-memory task API and evaluator doubles."""

import logging
import threading
from types import SimpleNamespace

import pytest

from app import worker as worker_module
from app.facts.extract import (
    FACTUAL_RESULT_VERSION,
    ExplanationRenderError,
    FactExtractionError,
    FactualResultValidationError,
)
from app.stockfish import EngineTimeout
from app.worker import (
    JsonLogFormatter,
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


class CapturingLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.exceptions = []

    def info(self, message, extra=None):
        self.infos.append((message, extra or {}))

    def warning(self, message, extra=None):
        self.warnings.append((message, extra or {}))

    def exception(self, message, extra=None):
        self.exceptions.append((message, extra or {}))


class RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def make_worker(tasks, evaluator, *, log=None):
    return Worker(
        session_factory=FakeSession,
        task_api=tasks,
        evaluator=evaluator,
        evaluation_config={"engine_path": "unused"},
        stop_event=threading.Event(),
        log=log,
    )


def test_json_log_formatter_emits_factual_elapsed():
    record = logging.LogRecord("worker", logging.INFO, __file__, 1, "worker transition", (), None)
    record.task_id = "t1"
    record.transition = "completed"
    record.elapsed = 1.23
    record.factual_elapsed = 0.12
    record.factual_version = FACTUAL_RESULT_VERSION
    payload = JsonLogFormatter().format(record)
    assert '"factual_elapsed":0.12' in payload
    assert f'"factual_version":{FACTUAL_RESULT_VERSION}' in payload


def test_worker_claims_evaluates_and_completes():
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    logger = CapturingLogger()
    worker = make_worker(tasks, lambda fen, config: 34, log=logger)
    from app import worker as worker_module

    build_calls = 0
    eval_calls = 0
    original_build = worker_module.build_factual_result

    def tracked_build(board):
        nonlocal build_calls
        build_calls += 1
        return original_build(board)

    def tracked_eval(fen, config):
        nonlocal eval_calls
        eval_calls += 1
        return 34

    worker.evaluator = tracked_eval
    worker_module.build_factual_result = tracked_build
    try:
        assert worker.process_once() is True
    finally:
        worker_module.build_factual_result = original_build
    assert tasks.recovered == 1
    assert build_calls == 1
    assert eval_calls == 1
    assert tasks.completed and tasks.completed[0][0:3] == ("t1", "l1", 34)
    assert tasks.completed[0][3]["version"] == 1
    assert tasks.completed[0][3]["facts"]["material"]["white"]["pawn"] == 8
    completed_logs = [extra for message, extra in logger.infos if extra.get("transition") == "completed"]
    assert completed_logs and completed_logs[0]["factual_elapsed"] >= 0


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


@pytest.mark.parametrize(
    "failure, expected_code",
    [
        (FactExtractionError("no facts"), "FACT_EXTRACTION_FAILED"),
        (ExplanationRenderError("no prose"), "EXPLANATION_RENDER_FAILED"),
        (FactualResultValidationError("bad bundle"), "FACTUAL_RESULT_INVALID"),
    ],
)
def test_factual_generation_failures_are_terminal_and_logged_with_traceback(failure, expected_code, monkeypatch):
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))

    logger = logging.getLogger(f"worker-test-{expected_code}")
    handler = RecordingHandler()
    logger.handlers[:] = []
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    eval_calls = 0

    def tracked_eval(fen, config):
        nonlocal eval_calls
        eval_calls += 1
        return 34

    original = worker_module.build_factual_result

    try:
        worker = make_worker(tasks, tracked_eval, log=logger)
        monkeypatch.setattr(
            worker_module,
            "build_factual_result",
            lambda board, exc=failure: (_ for _ in ()).throw(exc),
        )
        assert worker.process_once() is True
    finally:
        worker_module.build_factual_result = original
        logger.removeHandler(handler)

    assert eval_calls == 0
    assert tasks.retried == []
    assert tasks.failed == [("t1", expected_code)]

    factual_records = [record for record in handler.records if getattr(record, "transition", None) == "factual_generation_failed"]
    assert len(factual_records) == 1
    record = factual_records[0]
    assert record.levelno >= logging.ERROR
    assert record.exc_info is not None
    assert record.task_id == "t1"
    assert record.factual_version == FACTUAL_RESULT_VERSION
    assert record.failure_code == expected_code


def test_worker_reduces_the_engine_deadline_after_factual_generation(monkeypatch):
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    captured = {}

    def tracked_eval(fen, config):
        captured["config"] = config
        return 34

    worker = make_worker(
        tasks,
        tracked_eval,
        log=CapturingLogger(),
    )
    worker.evaluation_config = {
        "engine_path": "unused",
        "search_time_seconds": 1.0,
        "hard_attempt_timeout_seconds": 2.0,
        "engine_threads": 1,
        "engine_hash_mb": 64,
    }

    monotonic_values = iter([100.0, 100.0, 100.25, 100.4, 100.5, 100.6] + [100.6] * 20)
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: next(monotonic_values))

    assert worker.process_once() is True
    assert captured["config"]["hard_timeout"] == pytest.approx(1.6)
    assert captured["config"]["hard_attempt_timeout_seconds"] == pytest.approx(1.6)
    assert captured["config"]["search_time_seconds"] == 1.0
    assert tasks.completed and tasks.completed[0][3]["version"] == FACTUAL_RESULT_VERSION


def test_worker_times_out_without_invoking_the_evaluator_when_budget_is_exhausted(monkeypatch):
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    eval_calls = 0

    def tracked_eval(fen, config):
        nonlocal eval_calls
        eval_calls += 1
        return 34

    worker = make_worker(
        tasks,
        tracked_eval,
        log=CapturingLogger(),
    )
    worker.evaluation_config = {
        "engine_path": "unused",
        "search_time_seconds": 1.0,
        "hard_attempt_timeout_seconds": 1.0,
        "engine_threads": 1,
        "engine_hash_mb": 64,
    }

    monotonic_values = iter([200.0, 200.0, 200.8, 201.1, 201.2, 201.3] + [201.3] * 20)
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: next(monotonic_values))

    assert worker.process_once() is True
    assert eval_calls == 0
    assert tasks.retried == [("t1", "ENGINE_TIMEOUT", "The position could not be evaluated within the allowed time.")]
    assert tasks.failed == []
