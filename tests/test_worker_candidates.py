from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import chess

from app.candidates import CandidateReport, CandidateSnapshot, CpScore, FactChangeError, Provenance
import app.worker as worker_module
from app.worker import Worker, WorkerConfig, coerce_config


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
TERMINAL = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"


class _Session:
    def close(self):
        return None


class _Tasks:
    def __init__(self, fen):
        self.task = SimpleNamespace(id="task", fen=fen, attempts=1, lease_token="lease")
        self.completed = []
        self.failed = []

    def recover_expired_tasks(self, session):
        return None

    def claim_next_task(self, session):
        task, self.task = self.task, None
        return task

    def complete_task(self, session, task_id, lease_token, result, factual_result=None, **kwargs):
        self.completed.append((result, factual_result, kwargs))
        return True

    def fail_task(self, session, task_id, lease_token, error_code, error_message, **kwargs):
        self.failed.append(error_code)


def _provenance(fen: str, score: int = 34) -> Provenance:
    return Provenance(
        normalized_fen=fen,
        engine_build="fixture",
        network_hash=None,
        options={"Threads": 1, "Hash": 64, "MultiPV": 1},
        selected_depth=1,
        raw_scores=[CpScore(kind="cp", value=score)],
        original_pv_lengths=[1],
        elapsed_attempt_ms=1,
        elapsed_search_ms=1,
        elapsed_replay_ms=0,
        elapsed_render_ms=0,
        incomplete_groups=0,
        bound_only_groups=0,
        duplicate_root_groups=0,
        inconsistent_groups=0,
    )


def test_worker_candidate_adapter_invoked_once_and_rank_one_is_persisted():
    tasks = _Tasks(START)
    calls = []

    def evaluate_candidates(board, config):
        calls.append(board.fen(en_passant="fen"))
        return CandidateSnapshot(
            depth=1,
            reports=[CandidateReport(rank=1, depth=1, score=CpScore(kind="cp", value=34), pv=["e2e4"])],
        ), _provenance(START)

    worker = Worker(
        session_factory=_Session,
        task_api=tasks,
        candidate_evaluator=evaluate_candidates,
        evaluation_config={"candidate_target": 1, "max_continuation_plies": 6},
        stop_event=threading.Event(),
    )
    assert worker.process_once() is True
    assert len(calls) == 1
    assert tasks.completed[0][0] == 34
    assert tasks.completed[0][2]["candidate_result"]["moves"][0]["rank"] == 1


def test_terminal_position_skips_candidate_adapter():
    tasks = _Tasks(TERMINAL)
    calls = []

    def evaluate_candidates(board, config):
        calls.append(True)
        raise AssertionError("terminal roots must not search")

    worker = Worker(
        session_factory=_Session,
        task_api=tasks,
        candidate_evaluator=evaluate_candidates,
        evaluation_config={"candidate_target": 3},
        stop_event=threading.Event(),
    )
    assert worker.process_once() is True
    assert calls == []
    assert tasks.completed[0][2]["candidate_result"]["moves"] == []


def test_worker_config_candidate_defaults_and_bounds():
    config = coerce_config({})
    assert config.search_time == 3.0
    assert config.candidate_target == 3
    assert config.max_continuation_plies == 6
    assert WorkerConfig().threads == 1


def test_fact_change_failure_is_terminal_and_does_not_persist_partial_result(monkeypatch):
    tasks = _Tasks(START)

    def evaluate_candidates(board, config):
        return CandidateSnapshot(
            depth=1,
            reports=[CandidateReport(rank=1, depth=1, score=CpScore(kind="cp", value=34), pv=["e2e4"])],
        ), _provenance(START)

    def fail_build(*args, **kwargs):
        raise FactChangeError("fixture fact transition failure")

    monkeypatch.setattr(worker_module, "build_candidate_result", fail_build)
    worker = Worker(
        session_factory=_Session,
        task_api=tasks,
        candidate_evaluator=evaluate_candidates,
        evaluation_config={"candidate_target": 1, "max_continuation_plies": 6},
        stop_event=threading.Event(),
    )
    assert worker.process_once() is True
    assert tasks.completed == []
    assert tasks.failed == ["FACT_CHANGE_FAILED"]


def test_slow_final_candidate_validation_cannot_publish_after_deadline(monkeypatch):
    tasks = _Tasks(START)

    def evaluate_candidates(board, config):
        return CandidateSnapshot(
            depth=1,
            reports=[CandidateReport(rank=1, depth=1, score=CpScore(kind="cp", value=34), pv=["e2e4"])],
        ), _provenance(START)

    original_validate = worker_module.validate_candidate_result

    def slow_validate(bundle, board):
        time.sleep(0.08)
        return original_validate(bundle, board)

    monkeypatch.setattr(worker_module, "validate_candidate_result", slow_validate)
    worker = Worker(
        session_factory=_Session,
        task_api=tasks,
        candidate_evaluator=evaluate_candidates,
        evaluation_config={
            "candidate_target": 1,
            "max_continuation_plies": 6,
            "search_time_seconds": 0.01,
            "hard_attempt_timeout_seconds": 0.05,
        },
        stop_event=threading.Event(),
    )

    assert worker.process_once() is True
    assert tasks.completed == []
    assert tasks.failed == ["ENGINE_TIMEOUT"]
