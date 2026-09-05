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
from app.candidates import CandidateReport, CandidateSnapshot, CpScore, FactChangeError, MateScore, Provenance
from app.candidates.replay import InvalidEnginePV, ReplayStateMismatch
from app.stockfish import CandidateSearchResult, EngineTimeout, MateResult
from app.candidates.collect import CollectorDiagnostics
from app.worker import (
    JsonLogFormatter,
    Worker,
    WorkerConfig,
    WorkerConfigError,
    coerce_config,
    validate_startup_config,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
TERMINAL = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"


class FakeSession:
    def close(self):
        pass


class FakeTasks:
    def __init__(self, task):
        self.task = task
        self.recovered = 0
        self.claimed = 0
        self.completed = []
        self.completed_kwargs = []
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
        self.completed_kwargs.append(kwargs)
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


def candidate_provenance(fen=START, score=None, *, length=1, depth=1):
    score = score or CpScore(kind="cp", value=34)
    return Provenance(
        normalized_fen=fen,
        engine_build="fixture-engine",
        network_hash=None,
        options={"Threads": 1, "Hash": 64, "MultiPV": 1},
        selected_depth=depth,
        raw_scores=[score],
        original_pv_lengths=[length],
        elapsed_attempt_ms=0,
        elapsed_search_ms=1,
        elapsed_replay_ms=0,
        elapsed_render_ms=0,
        incomplete_groups=0,
        bound_only_groups=0,
        duplicate_root_groups=0,
        inconsistent_groups=0,
    )


def candidate_worker(tasks, evaluator, *, fen=START, config=None, log=None):
    return Worker(
        session_factory=FakeSession,
        task_api=tasks,
        candidate_evaluator=evaluator,
        evaluation_config=config or {"engine_path": "unused", "candidate_target": 1},
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


def test_startup_requires_search_time_margin_before_hard_timeout():
    with pytest.raises(WorkerConfigError, match="cleanup margin"):
        validate_startup_config(WorkerConfig(search_time=1.0, hard_timeout=1.0), check_engine=False)


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
    assert 0 < captured["config"]["hard_timeout"] < 2.0
    assert captured["config"]["hard_attempt_timeout_seconds"] == captured["config"]["hard_timeout"]
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


def test_candidate_search_result_is_persisted_atomically_with_rank_one_and_timings(monkeypatch):
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    calls = []

    def candidate_adapter(board, config):
        calls.append(board.fen(en_passant="fen"))
        return CandidateSearchResult(
            snapshot=CandidateSnapshot(
                depth=12,
                reports=[CandidateReport(rank=1, depth=12, score=CpScore(kind="cp", value=34), pv=["e2e4"])]
            ),
            engine_build="Stockfish fixture",
            network_hash="nnue-test",
            options={"Threads": 1, "Hash": 64, "MultiPV": 1},
            elapsed_search_ms=17,
            diagnostics=CollectorDiagnostics(),
        )

    worker = candidate_worker(tasks, candidate_adapter)
    assert worker.process_once() is True
    assert calls == [START]
    assert tasks.completed[0][2] == 34
    assert tasks.completed_kwargs[0]["candidate_result"]["moves"][0]["rank"] == 1
    assert tasks.completed_kwargs[0]["candidate_result"]["provenance"]["engine_build"] == "Stockfish fixture"
    provenance = tasks.completed_kwargs[0]["candidate_result"]["provenance"]
    assert provenance["elapsed_search_ms"] == 17
    assert provenance["elapsed_attempt_ms"] >= 0
    assert provenance["elapsed_replay_ms"] >= 0
    assert provenance["elapsed_render_ms"] >= 0


def test_terminal_candidate_path_persists_explicit_empty_bundle_without_search():
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=TERMINAL, attempts=1, lease_token="l1"))
    calls = []

    def adapter(board, config):
        calls.append(True)
        raise AssertionError("terminal roots must not invoke the candidate engine")

    worker = candidate_worker(tasks, adapter)
    assert worker.process_once() is True
    assert calls == []
    candidate_result = tasks.completed_kwargs[0]["candidate_result"]
    assert candidate_result["moves"] == []
    assert candidate_result["analysis"]["selection_policy"] == "terminal_position"
    assert candidate_result["provenance"]["selected_depth"] is None
    assert candidate_result["provenance"]["raw_scores"] == []
    assert candidate_result["provenance"]["original_pv_lengths"] == []


def test_terminal_root_does_not_require_a_full_search_budget(monkeypatch):
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=TERMINAL, attempts=1, lease_token="l1"))
    clock_values = iter([0.0, 0.0, 9.5])

    def clock():
        try:
            return next(clock_values)
        except StopIteration:
            return 9.5

    monkeypatch.setattr(worker_module.time, "monotonic", clock)
    monkeypatch.setattr(
        worker_module,
        "build_factual_result",
        lambda board: SimpleNamespace(model_dump=lambda mode="json": {"version": 1}),
    )
    calls = []
    worker = candidate_worker(tasks, lambda board, config: calls.append(True))
    assert worker.process_once() is True
    assert calls == []
    assert tasks.completed_kwargs[0]["candidate_result"]["moves"] == []


def test_production_candidate_adapter_is_selected_when_no_custom_evaluator(monkeypatch):
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    calls = []

    def production_adapter(board, config):
        calls.append(board.fen(en_passant="fen"))
        return CandidateSearchResult(
            snapshot=CandidateSnapshot(
                depth=1,
                reports=[CandidateReport(rank=1, depth=1, score=CpScore(kind="cp", value=10), pv=["e2e4"])]
            ),
            engine_build="same-process-build",
            network_hash=None,
            options={"Threads": 1, "Hash": 64, "MultiPV": 1},
            elapsed_search_ms=2,
            diagnostics=CollectorDiagnostics(),
        )

    monkeypatch.setattr(worker_module, "evaluate_candidates", production_adapter)
    worker = Worker(
        session_factory=FakeSession,
        task_api=tasks,
        evaluation_config={"engine_path": "unused", "candidate_target": 1},
        stop_event=threading.Event(),
    )
    assert worker.process_once() is True
    assert calls == [START]
    assert tasks.completed[0][2] == 10


def test_black_to_move_mate_result_and_rank_one_agreement():
    black_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=black_fen, attempts=1, lease_token="l1"))

    def black_adapter(board, config):
        return CandidateSnapshot(
            depth=2,
            reports=[CandidateReport(rank=1, depth=2, score=CpScore(kind="cp", value=-40), pv=["d7d5"])]
        ), candidate_provenance(black_fen, CpScore(kind="cp", value=-40), depth=2)

    worker = candidate_worker(tasks, black_adapter, config={"engine_path": "unused", "candidate_target": 1})
    assert worker.process_once() is True
    assert tasks.completed[0][2] == -40
    assert tasks.completed_kwargs[0]["candidate_result"]["analysis"]["root_side"] == "black"

    mate_fen = "4k3/8/8/8/8/8/4K3/6R1 w - - 0 1"
    mate_tasks = FakeTasks(SimpleNamespace(id="t2", fen=mate_fen, attempts=1, lease_token="l2"))

    def mate_adapter(board, config):
        score = MateScore(kind="mate", winner="white", moves=1)
        return CandidateSnapshot(depth=1, reports=[CandidateReport(rank=1, depth=1, score=score, pv=["g1g8"])]), candidate_provenance(mate_fen, score)

    mate_worker = candidate_worker(mate_tasks, mate_adapter, config={"engine_path": "unused", "candidate_target": 1})
    assert mate_worker.process_once() is True
    assert isinstance(mate_tasks.completed[0][2], MateResult)
    assert mate_tasks.completed[0][2].winner == "white"
    assert mate_tasks.completed[0][2].moves == 1
    assert mate_tasks.completed_kwargs[0]["mate_winner"] == "white"
    assert mate_tasks.completed_kwargs[0]["mate_moves"] == 1


@pytest.mark.parametrize("failure", [ReplayStateMismatch("bad state"), FactChangeError("bad facts")])
def test_candidate_state_failures_are_non_retryable(failure):
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))

    def failing_adapter(board, config):
        raise failure

    worker = candidate_worker(tasks, failing_adapter)
    assert worker.process_once() is True
    assert tasks.retried == []
    assert tasks.failed == [("t1", failure.code)]
    assert tasks.completed == []


def test_invalid_engine_pv_retries_once():
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))

    def failing_adapter(board, config):
        raise InvalidEnginePV("bad pv")

    worker = candidate_worker(tasks, failing_adapter)
    assert worker.process_once() is True
    assert tasks.retried == [("t1", "INVALID_ENGINE_PV", "The chess engine returned an invalid continuation.")]
    assert tasks.failed == []


def test_candidate_budget_exhaustion_does_not_call_adapter(monkeypatch):
    tasks = FakeTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    calls = []

    def adapter(board, config):
        calls.append(True)
        raise AssertionError("budget exhaustion must precede engine invocation")

    worker = candidate_worker(
        tasks,
        adapter,
        config={"engine_path": "unused", "candidate_target": 1, "search_time_seconds": 0.000001, "hard_attempt_timeout_seconds": 0.000001},
    )
    assert worker.process_once() is True
    assert calls == []
    assert tasks.retried == [("t1", "ENGINE_TIMEOUT", "The position could not be evaluated within the allowed time.")]


def test_candidate_stale_completion_is_discarded_without_retry():
    class StaleTasks(FakeTasks):
        def complete_task(self, session, task_id, lease_token, result, factual_result=None, **kwargs):
            self.completed_kwargs.append(kwargs)
            return False

    tasks = StaleTasks(SimpleNamespace(id="t1", fen=START, attempts=1, lease_token="l1"))
    worker = candidate_worker(tasks, lambda board, config: (CandidateSnapshot(depth=1, reports=[CandidateReport(rank=1, depth=1, score=CpScore(kind="cp", value=34), pv=["e2e4"])]), candidate_provenance()))
    assert worker.process_once() is True
    assert tasks.retried == []
    assert tasks.completed_kwargs[0]["candidate_result"]["moves"][0]["rank"] == 1


def test_candidate_config_bounds_are_enforced():
    with pytest.raises(WorkerConfigError):
        validate_startup_config(WorkerConfig(candidate_target=0), check_engine=False)
    with pytest.raises(WorkerConfigError):
        validate_startup_config(WorkerConfig(max_continuation_plies=7), check_engine=False)
    with pytest.raises(WorkerConfigError):
        validate_startup_config(WorkerConfig(hash_mb=63), check_engine=False)
