"""Durable one-at-a-time task worker.

Expected ``app.tasks`` integration (the repository layer owns transactions
and SQL details):

* ``recover_expired_tasks(session)`` requeues/terminally fails expired leases;
* ``claim_next_task(session)`` atomically claims the oldest queued row and
  returns it with ``id``, ``fen``, ``attempts``, ``lease_token`` and optional
  ``evaluation_config``;
* ``complete_task(...)`` and ``retry_task(...)``/``requeue_task(...)`` require
  the task id, lease token, and current lease.  ``fail_task(...)`` is the
  terminal counterpart.  Every write is expected to guard on running status,
  matching token, and an unexpired lease, so stale workers cannot overwrite a
  newer attempt.

The small invocation adapter below accepts either these module functions or a
repository object exposing equivalent methods.  It is intentionally kept here
to avoid imposing a session/transaction shape on ``app.tasks``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import shutil
import signal
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import chess

from .constants import ENGINE_CLEANUP_MARGIN
from .candidates import (
    CandidateReport,
    CandidateResult,
    CandidateResultValidationError,
    CandidateSnapshot,
    CandidateScore,
    CpScore,
    FactChangeError,
    MateScore,
    Provenance,
    build_candidate_result,
    rank_one_display,
)
from .candidates.validate import validate_candidate_result

from .facts.extract import (
    ExplanationRenderError,
    FactExtractionError,
    FactualResultValidationError,
    FACTUAL_RESULT_VERSION,
    build_factual_result,
)
from .stockfish import (
    CandidateSearchResult,
    EngineError,
    EngineTimeout,
    InvalidEngineConfig,
    MateResult,
    evaluate_fen,
)

logger = logging.getLogger(__name__)

# Populated by newer stockfish adapters; kept as a narrow injection seam for
# tests and for rolling upgrades where the adapter module is replaced first.
evaluate_candidates = None


@dataclass(frozen=True)
class WorkerConfig:
    engine_path: str = "stockfish"
    search_time: float = 3.0
    hard_timeout: float = 10.0
    threads: int = 1
    hash_mb: int = 64
    skill: int = 20
    poll_interval: float = 0.5
    lease_seconds: float = 30.0
    max_attempts: int = 2
    candidate_target: int = 3
    max_continuation_plies: int = 6


class WorkerConfigError(ValueError):
    pass


SAFE_ERROR_MESSAGES = {
    "ENGINE_TIMEOUT": "The position could not be evaluated within the allowed time.",
    "ENGINE_CRASH": "The chess engine was unavailable while evaluating the position.",
    "ENGINE_SCORE_MISSING": "The chess engine returned no usable evaluation.",
    "ENGINE_CONFIG_INVALID": "The chess engine configuration is invalid.",
    "ENGINE_ERROR": "The position could not be evaluated.",
    "FACT_EXTRACTION_FAILED": "The position facts could not be generated.",
    "EXPLANATION_RENDER_FAILED": "The factual explanation could not be generated.",
    "FACTUAL_RESULT_INVALID": "The factual result could not be validated.",
    "FACT_CHANGE_FAILED": "The candidate facts could not be validated.",
    "INCOMPLETE_CANDIDATE_SET": "The chess engine did not return a complete candidate set.",
    "INVALID_ENGINE_PV": "The chess engine returned an invalid continuation.",
    "INVALID_CANDIDATE_SCORE": "The chess engine returned an invalid candidate score.",
    "REPLAY_STATE_MISMATCH": "The candidate continuation could not be validated.",
    "CANDIDATE_RENDER_FAILED": "The candidate description could not be generated.",
    "CANDIDATE_RESULT_INVALID": "The candidate result could not be validated.",
}

NON_RETRYABLE_FAILURE_CODES = {
    "ENGINE_CONFIG_INVALID",
    "FACT_EXTRACTION_FAILED",
    "EXPLANATION_RENDER_FAILED",
    "FACTUAL_RESULT_INVALID",
    "FACT_CHANGE_FAILED",
    "REPLAY_STATE_MISMATCH",
    "CANDIDATE_RENDER_FAILED",
    "CANDIDATE_RESULT_INVALID",
}


class JsonLogFormatter(logging.Formatter):
    """Compact JSON logs retaining worker transition context."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in (
            "task_id",
            "attempt",
            "transition",
            "elapsed",
            "factual_elapsed",
            "factual_version",
            "failure_code",
            "engine_version",
            "poll_interval",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging() -> None:
    """Install structured stderr logging for the standalone worker process."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def _value(config: Any, *names: str, default: Any = None) -> Any:
    if isinstance(config, Mapping):
        for name in names:
            if name in config:
                return config[name]
    else:
        for name in names:
            if hasattr(config, name):
                return getattr(config, name)
    return default


def coerce_config(config: Any = None) -> WorkerConfig:
    if isinstance(config, WorkerConfig):
        return config
    return WorkerConfig(
        engine_path=os.fspath(_value(config, "engine_path", "stockfish_path", default="stockfish")),
        search_time=float(_value(config, "search_time", "engine_time", "time_limit", "engine_time_limit", "engine_time_seconds", "search_time_seconds", default=3.0)),
        hard_timeout=float(_value(config, "hard_timeout", "attempt_timeout", "hard_attempt_timeout", "engine_hard_timeout", "hard_timeout_seconds", "hard_attempt_timeout_seconds", default=10.0)),
        threads=int(_value(config, "threads", "engine_threads", "stockfish_threads", default=1)),
        hash_mb=int(_value(config, "hash_mb", "hash_memory_mb", "hash", "stockfish_hash_mb", "engine_hash_mb", default=64)),
        skill=int(_value(config, "skill", "skill_level", default=20)),
        poll_interval=float(_value(config, "poll_interval", "worker_poll_interval", "poll_interval_seconds", default=0.5)),
        lease_seconds=float(_value(config, "lease_seconds", "task_lease_seconds", default=30.0)),
        max_attempts=int(_value(config, "max_attempts", "worker_max_attempts", default=2)),
        candidate_target=int(_value(config, "candidate_target", "requested_count", default=3)),
        max_continuation_plies=int(_value(config, "max_continuation_plies", "continuation_cap", default=6)),
    )


def validate_startup_config(config: Any = None, *, check_engine: bool = True) -> WorkerConfig:
    """Validate deployment settings before claiming any task."""
    c = coerce_config(config)
    if c.search_time <= 0 or c.hard_timeout <= 0 or c.hard_timeout - c.search_time < ENGINE_CLEANUP_MARGIN:
        raise WorkerConfigError("hard timeout must exceed search time by at least the cleanup margin")
    if c.hard_timeout >= c.lease_seconds:
        raise WorkerConfigError("hard timeout must be shorter than the task lease")
    if c.threads != 1:
        raise WorkerConfigError("MVP worker requires exactly one engine thread")
    if c.hash_mb != 64 or not 0 <= c.skill <= 20:
        raise WorkerConfigError("invalid fixed Stockfish settings (Threads=1, Hash=64)")
    if c.poll_interval <= 0 or c.max_attempts != 2:
        raise WorkerConfigError("MVP polling/attempt settings are fixed at 500ms/two attempts")
    if not 1 <= c.candidate_target <= 3:
        raise WorkerConfigError("candidate target must be between one and three")
    if not 1 <= c.max_continuation_plies <= 6:
        raise WorkerConfigError("continuation cap must be between one and six")
    if check_engine and not _engine_available(c.engine_path):
        raise WorkerConfigError(f"Stockfish executable is unavailable: {c.engine_path}")
    return c


def _engine_available(path: str) -> bool:
    if os.path.dirname(path):
        return os.path.isfile(path) and os.access(path, os.X_OK)
    return shutil.which(path) is not None


def _task_value(task: Any, *names: str, default: Any = None) -> Any:
    if isinstance(task, Mapping):
        for name in names:
            if name in task:
                return task[name]
    else:
        for name in names:
            if hasattr(task, name):
                return getattr(task, name)
    return default


def _invoke(fn: Callable[..., Any], session: Any, positional: tuple[Any, ...], values: Mapping[str, Any]) -> Any:
    """Call task APIs with named arguments where possible, preserving mocks."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(session, *positional)
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs = dict(values)
    else:
        kwargs = {key: value for key, value in values.items() if key in params}
    # Session is conventionally the first argument, but support db/repository
    # names as well without requiring a particular ORM.
    session_name = next((n for n in ("session", "db", "connection", "s") if n in params), None)
    if session_name:
        kwargs[session_name] = session
    required_pos = [p for p in params.values() if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if not session_name and required_pos:
        # A zero-positional operation (recover/claim) still needs the session
        # when a repository uses a short parameter name such as ``s``.  For
        # result mutations, positional already starts with task_id and the
        # method is treated as a session-free repository API.
        return fn(session, *positional) if not positional else fn(*positional)
    try:
        return fn(**kwargs)
    except TypeError:
        # A concise fallback for positional-only or legacy repository methods.
        return fn(session, *positional)


class Worker:
    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
        evaluation_config: Any = None,
        evaluator: Callable[[str, Any], int | MateResult] = evaluate_fen,
        candidate_evaluator: Callable[..., Any] | None = None,
        task_api: Any = None,
        poll_interval: float | None = None,
        stop_event: threading.Event | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.evaluation_config = evaluation_config
        self.evaluator = evaluator
        self.candidate_evaluator = candidate_evaluator
        self.task_api = task_api
        self.stop_event = stop_event or threading.Event()
        self.log = log or logger
        self._poll_interval = poll_interval
        self._handlers_installed = False
        self._db_blocked = False

    def _api(self) -> Any:
        if self.task_api is not None:
            return self.task_api
        from . import tasks
        return tasks

    def _session(self) -> tuple[Any, Callable[[], None]]:
        factory = self.session_factory
        if factory is None:
            try:
                from .db import SessionLocal
            except ImportError as exc:  # pragma: no cover - startup wiring error
                try:
                    from .db import SessionFactory as SessionLocal
                except ImportError:
                    raise RuntimeError("worker requires app.db.SessionFactory or session_factory") from exc
            factory = SessionLocal
        session = factory()
        if hasattr(session, "__enter__") and hasattr(session, "__exit__"):
            entered = session.__enter__()
            return entered, lambda: session.__exit__(None, None, None)
        def close() -> None:
            try:
                if hasattr(session, "close"):
                    self._resolve(session.close())
            except Exception:
                pass
        return session, close

    @staticmethod
    def _resolve(value: Any) -> Any:
        """Resolve async repository calls for this synchronous process loop."""
        if inspect.isawaitable(value):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(value)
            raise RuntimeError("worker's synchronous loop cannot run inside an event loop")
        return value

    def _transaction(self, action: Callable[[Any], Any]) -> Any:
        session, close = self._session()
        try:
            return self._resolve(action(session))
        finally:
            close()

    def _commit(self, session: Any) -> None:
        # Repository functions normally commit themselves.  A direct fake or
        # lightweight SQLAlchemy repository may leave it to the caller.
        commit = getattr(session, "commit", None)
        if commit:
            commit()

    def _recover(self) -> Any:
        api = self._api()
        fn = getattr(api, "recover_expired_tasks", None) or getattr(api, "recover_expired_running_tasks", None) or getattr(api, "recover_expired", None)
        fn = fn or getattr(api, "recover_leases", None)
        if fn is None:
            return None
        c = coerce_config(self.evaluation_config)
        return self._transaction(lambda s: _invoke(fn, s, (), {"now": None, "max_attempts": c.max_attempts}))

    def _claim(self) -> Any:
        api = self._api()
        fn = getattr(api, "claim_next_task", None) or getattr(api, "claim_queued_task", None) or getattr(api, "claim_one", None) or getattr(api, "claim_task", None)
        if fn is None:
            raise RuntimeError("app.tasks must provide claim_next_task")
        c = coerce_config(self.evaluation_config)
        return self._transaction(lambda s: _invoke(fn, s, (), {"lease_seconds": c.lease_seconds}))

    def _persist_success(
        self,
        task: Any,
        result: int | MateResult,
        factual_result: dict[str, Any],
        started: float,
        candidate_result: dict[str, Any] | None = None,
    ) -> bool:
        api = self._api()
        fn = getattr(api, "complete_task", None) or getattr(api, "complete", None)
        if fn is None:
            raise RuntimeError("app.tasks must provide complete_task")
        task_id = _task_value(task, "id", "task_id")
        token = _task_value(task, "lease_token", "attempt_token")
        values: dict[str, Any] = {
            "task_id": task_id,
            "id": task_id,
            "lease_token": token,
            "attempt_token": token,
            "result": result,
            "evaluation": result if isinstance(result, int) else None,
            "evaluation_cp": result if isinstance(result, int) else None,
            "mate_winner": result.winner if isinstance(result, MateResult) else None,
            "mate_moves": result.moves if isinstance(result, MateResult) else None,
            "engine_version": getattr(self, "engine_version", None),
            "factual_result": factual_result,
            "candidate_result": candidate_result,
            "elapsed": time.monotonic() - started,
        }
        persisted = self._transaction(lambda s: _invoke(fn, s, (task_id, token, result), values))
        if persisted is False:
            self.log.warning("discarded stale worker result", extra={"task_id": str(task_id), "transition": "stale_result_discarded", "elapsed": time.monotonic() - started})
            return False
        return True

    def _persist_failure(self, task: Any, error: BaseException, started: float, *, retryable: bool = True) -> None:
        api = self._api()
        task_id = _task_value(task, "id", "task_id")
        token = _task_value(task, "lease_token", "attempt_token")
        attempts = int(_task_value(task, "attempts", "attempt", default=1) or 1)
        code = getattr(error, "code", "ENGINE_ERROR")
        message = SAFE_ERROR_MESSAGES.get(code, SAFE_ERROR_MESSAGES["ENGINE_ERROR"])
        common = {"task_id": task_id, "id": task_id, "lease_token": token, "attempt_token": token, "error_code": code, "code": code, "error_message": message, "message": message, "elapsed": time.monotonic() - started, "terminal": not retryable}
        if retryable and attempts < 2:
            fn = getattr(api, "retry_task", None) or getattr(api, "requeue_task", None) or getattr(api, "retry_or_fail_task", None) or getattr(api, "fail_or_retry", None)
            if fn is not None:
                self._transaction(lambda s: _invoke(fn, s, (task_id, token, code, message), common))
                return
        fn = getattr(api, "fail_task", None) or getattr(api, "fail", None)
        if fn is None:
            raise RuntimeError("app.tasks must provide fail_task")
        self._transaction(lambda s: _invoke(fn, s, (task_id, token, code, message), common))

    @staticmethod
    def _bounded_evaluation_config(config: Any, hard_timeout_seconds: float) -> dict[str, Any]:
        bounded = max(0.01, hard_timeout_seconds)
        return {
            "engine_path": _value(config, "engine_path", default="stockfish"),
            "engine_version": _value(config, "engine_version", default=None),
            "search_time_seconds": _value(
                config,
                "search_time_seconds",
                "search_time",
                "engine_time",
                "time_limit",
                "engine_time_limit",
                default=3.0,
            ),
            "search_time": _value(
                config,
                "search_time",
                "engine_time",
                "time_limit",
                "engine_time_limit",
                "search_time_seconds",
                default=3.0,
            ),
            "hard_timeout": bounded,
            "hard_attempt_timeout_seconds": bounded,
            "attempt_timeout": bounded,
            "engine_threads": _value(config, "engine_threads", "threads", default=1),
            "threads": _value(config, "threads", "engine_threads", default=1),
            "engine_hash_mb": _value(config, "engine_hash_mb", "hash_mb", "hash_memory_mb", "hash", default=64),
            "hash_mb": _value(config, "hash_mb", "engine_hash_mb", "hash_memory_mb", "hash", default=64),
            "skill": _value(config, "skill", "skill_level", default=20),
            "candidate_target": _value(config, "candidate_target", "requested_count", default=3),
            "max_continuation_plies": _value(config, "max_continuation_plies", "continuation_cap", default=6),
        }

    @staticmethod
    def _terminal_result(board: chess.Board) -> int | MateResult:
        if board.is_checkmate():
            return MateResult("white" if board.turn == chess.BLACK else "black", 0)
        return 0

    def _candidate_adapter(self) -> Callable[..., Any] | None:
        if self.candidate_evaluator is not None:
            return self.candidate_evaluator
        # A caller-provided evaluator is an explicit compatibility/test seam;
        # it must not be shadowed by the production adapter discovered below.
        if self.evaluator is not evaluate_fen:
            return self.evaluator
        if evaluate_candidates is not None:
            return evaluate_candidates
        try:
            from . import stockfish

            adapter = getattr(stockfish, "evaluate_candidates", None)
            if adapter is not None:
                return adapter
        except ImportError:
            pass
        return None

    @staticmethod
    def _provenance_for_snapshot(board: chess.Board, snapshot: CandidateSnapshot, config: Any, metadata: Any = None) -> Provenance:
        metadata = metadata if isinstance(metadata, Mapping) else {}
        options = metadata.get("options") or {
            "Threads": int(_value(config, "threads", "engine_threads", default=1)),
            "Hash": int(_value(config, "hash_mb", "engine_hash_mb", default=64)),
            "MultiPV": int(_value(config, "candidate_target", "requested_count", default=3)),
        }
        return Provenance(
            normalized_fen=board.fen(en_passant="fen"),
            engine_build=str(metadata.get("engine_build") or _value(config, "engine_version", default=None) or "custom"),
            network_hash=metadata.get("network_hash"),
            options=options,
            selected_depth=snapshot.depth,
            raw_scores=[report.score for report in snapshot.reports],
            original_pv_lengths=[len(report.pv) for report in snapshot.reports],
            elapsed_attempt_ms=int(metadata.get("elapsed_attempt_ms", 0)),
            elapsed_search_ms=int(metadata.get("elapsed_search_ms", 0)),
            elapsed_replay_ms=int(metadata.get("elapsed_replay_ms", 0)),
            elapsed_render_ms=int(metadata.get("elapsed_render_ms", 0)),
            incomplete_groups=int(metadata.get("incomplete_groups", 0)),
            bound_only_groups=int(metadata.get("bound_only_groups", 0)),
            duplicate_root_groups=int(metadata.get("duplicate_root_groups", 0)),
            inconsistent_groups=int(metadata.get("inconsistent_groups", 0)),
        )

    def _candidate_attempt(self, board: chess.Board, config: Any, started: float, deadline: float) -> tuple[int | MateResult, CandidateResult]:
        adapter = self._candidate_adapter()
        if adapter is None:
            raise EngineError("candidate adapter is unavailable")
        bounded = self._bounded_evaluation_config(config, max(0.01, deadline - time.monotonic()))
        try:
            try:
                parameters = list(inspect.signature(adapter).parameters.values())
            except (TypeError, ValueError):
                parameters = []
            first_name = parameters[0].name.casefold() if parameters else ""
            board_arg = board.fen(en_passant="fen") if first_name in {"fen", "fen_text", "position_fen"} else board
            raw = adapter(board_arg, bounded)
        except Exception:
            raise
        metadata = None
        if isinstance(raw, CandidateSearchResult) or (
            hasattr(raw, "snapshot")
            and hasattr(raw, "engine_build")
            and hasattr(raw, "elapsed_search_ms")
        ):
            diagnostics = getattr(raw, "diagnostics", None)
            metadata = {
                "engine_build": getattr(raw, "engine_build", None),
                "network_hash": getattr(raw, "network_hash", None),
                "options": getattr(raw, "options", None),
                "elapsed_search_ms": getattr(raw, "elapsed_search_ms", 0),
                "incomplete_groups": getattr(diagnostics, "incomplete_groups", 0),
                "bound_only_groups": getattr(diagnostics, "bound_only_groups", 0),
                "duplicate_root_groups": getattr(diagnostics, "duplicate_root_groups", 0),
                "inconsistent_groups": getattr(diagnostics, "inconsistent_groups", 0),
            }
            raw = getattr(raw, "snapshot", None)
        if isinstance(raw, CandidateResult):
            bundle = validate_candidate_result(raw, board)
            display_eval, display_mate = rank_one_display(bundle)
            if display_mate is not None:
                return MateResult(display_mate.winner, display_mate.moves), bundle
            return int(round((display_eval or 0) * 100)), bundle
        if isinstance(raw, tuple) and len(raw) == 2:
            raw, metadata = raw
        if isinstance(raw, Mapping):
            metadata = raw.get("provenance", metadata)
            raw = raw.get("snapshot") or raw.get("candidate_snapshot") or raw.get("reports")
        if isinstance(raw, CandidateSnapshot):
            snapshot = raw
        elif isinstance(raw, list):
            snapshot = CandidateSnapshot(depth=int(_value(config, "depth", default=1)), reports=raw)
        elif isinstance(raw, (int, MateResult)):
            # Legacy injected evaluators remain usable in tests; production's
            # default path requires the candidate adapter above.
            target = int(_value(config, "candidate_target", "requested_count", default=3))
            legal = list(board.legal_moves)[:target]
            score = MateScore(kind="mate", winner=raw.winner, moves=raw.moves) if isinstance(raw, MateResult) else CpScore(kind="cp", value=raw)
            snapshot = CandidateSnapshot(
                depth=1,
                reports=[CandidateReport(rank=i + 1, depth=1, score=score, pv=[move.uci()]) for i, move in enumerate(legal)],
            )
        else:
            raise EngineError("candidate adapter returned an unusable result")
        provenance = metadata if isinstance(metadata, Provenance) else self._provenance_for_snapshot(board, snapshot, config, metadata)
        target = int(_value(config, "candidate_target", "requested_count", default=3))
        max_plies = int(_value(config, "max_continuation_plies", "continuation_cap", default=6))
        budget_ms = int(float(_value(config, "search_time", "search_time_seconds", default=3.0)) * 1000)
        timing_sink: dict[str, int] = {}
        bundle = build_candidate_result(
            board,
            snapshot,
            provenance,
            requested_count=target,
            search_budget_ms=budget_ms,
            max_continuation_plies=max_plies,
            timing_sink=timing_sink,
        )
        if time.monotonic() >= deadline:
            raise EngineTimeout("candidate processing exceeded attempt budget")
        measured = bundle.provenance.model_copy(
            update={
                "elapsed_attempt_ms": max(0, int(round((time.monotonic() - started) * 1000))),
                "elapsed_replay_ms": timing_sink.get("replay_ms", 0),
                "elapsed_render_ms": timing_sink.get("render_ms", 0),
            }
        )
        bundle = bundle.model_copy(update={"provenance": measured})
        if time.monotonic() >= deadline:
            raise EngineTimeout("candidate validation exceeded attempt budget")
        bundle = validate_candidate_result(bundle, board)
        if time.monotonic() >= deadline:
            raise EngineTimeout("candidate validation exceeded attempt budget")
        display_eval, display_mate = rank_one_display(bundle)
        if display_mate is not None:
            return MateResult(display_mate.winner, display_mate.moves), bundle
        return int(round((display_eval or 0) * 100)), bundle

    def process_once(self) -> bool:
        """Recover, claim, evaluate and persist at most one task."""
        if self._db_blocked:
            # A successful recovery call is a cheap database reachability
            # probe.  Do not claim any new work until it succeeds.
            self._recover()
            self._db_blocked = False
        self._recover()
        task = self._claim()
        if task is None:
            return False
        task_id = _task_value(task, "id", "task_id")
        attempts = _task_value(task, "attempts", "attempt", default=1)
        fen = _task_value(task, "fen")
        config = _task_value(task, "evaluation_config", "config", default=self.evaluation_config)
        started = time.monotonic()
        self.log.info(
            "worker transition",
            extra={
                "task_id": str(task_id),
                "attempt": attempts,
                "transition": "claimed",
                "elapsed": 0.0,
                "factual_version": FACTUAL_RESULT_VERSION,
            },
        )
        try:
            factual_started = time.monotonic()
            board = chess.Board(fen)
            factual_bundle = build_factual_result(board)
            factual_elapsed = time.monotonic() - factual_started
        except (FactExtractionError, ExplanationRenderError, FactualResultValidationError) as exc:
            factual_elapsed = time.monotonic() - factual_started
            code = getattr(exc, "code", "FACT_EXTRACTION_FAILED")
            self.log.exception(
                "worker transition",
                extra={
                    "task_id": str(task_id),
                    "attempt": attempts,
                    "transition": "factual_generation_failed",
                    "failure_code": code,
                    "elapsed": time.monotonic() - started,
                    "factual_elapsed": factual_elapsed,
                    "factual_version": FACTUAL_RESULT_VERSION,
                },
            )
            try:
                self._persist_failure(task, exc, started, retryable=False)
                self.log.info(
                    "worker transition",
                    extra={
                        "task_id": str(task_id),
                        "attempt": attempts,
                        "transition": "failed",
                        "failure_code": code,
                        "elapsed": time.monotonic() - started,
                        "factual_elapsed": factual_elapsed,
                        "factual_version": FACTUAL_RESULT_VERSION,
                    },
                )
            except Exception:
                self._db_blocked = True
                self.log.exception(
                    "could not persist factual failure",
                    extra={
                        "task_id": str(task_id),
                        "attempt": attempts,
                        "transition": "persistence_failed",
                        "failure_code": "DB_UNAVAILABLE",
                        "elapsed": time.monotonic() - started,
                        "factual_elapsed": factual_elapsed,
                        "factual_version": FACTUAL_RESULT_VERSION,
                    },
                )
                raise
            return True
        except Exception as exc:
            factual_elapsed = time.monotonic() - factual_started
            code = getattr(exc, "code", "FACT_EXTRACTION_FAILED")
            self.log.exception(
                "worker transition",
                extra={
                    "task_id": str(task_id),
                    "attempt": attempts,
                    "transition": "factual_generation_failed",
                    "failure_code": code,
                    "elapsed": time.monotonic() - started,
                    "factual_elapsed": factual_elapsed,
                    "factual_version": FACTUAL_RESULT_VERSION,
                },
            )
            try:
                self._persist_failure(task, exc, started, retryable=False)
                self.log.info(
                    "worker transition",
                    extra={
                        "task_id": str(task_id),
                        "attempt": attempts,
                        "transition": "failed",
                        "failure_code": code,
                        "elapsed": time.monotonic() - started,
                        "factual_elapsed": factual_elapsed,
                        "factual_version": FACTUAL_RESULT_VERSION,
                    },
                )
            except Exception:
                self._db_blocked = True
                self.log.exception(
                    "could not persist factual failure",
                    extra={
                        "task_id": str(task_id),
                        "attempt": attempts,
                        "transition": "persistence_failed",
                        "failure_code": "DB_UNAVAILABLE",
                        "elapsed": time.monotonic() - started,
                        "factual_elapsed": factual_elapsed,
                        "factual_version": FACTUAL_RESULT_VERSION,
                    },
                )
                raise
            return True
        try:
            try:
                hard_timeout_seconds = float(_value(config, "hard_timeout", "attempt_timeout", "hard_attempt_timeout", "engine_hard_timeout", "hard_timeout_seconds", "hard_attempt_timeout_seconds", default=10.0))
                search_time_seconds = float(_value(config, "search_time", "engine_time", "time_limit", "engine_time_limit", "search_time_seconds", default=3.0))
            except (TypeError, ValueError) as exc:
                raise InvalidEngineConfig("invalid worker evaluation configuration") from exc
            absolute_deadline = started + hard_timeout_seconds
            if time.monotonic() >= absolute_deadline:
                raise EngineTimeout("factual generation exhausted the attempt budget")
            terminal_root = (
                board.is_checkmate()
                or board.is_stalemate()
                or board.is_insufficient_material()
                or getattr(board, "is_seventyfive_moves", lambda: False)()
                or getattr(board, "is_variant_draw", lambda: False)()
            )
            if terminal_root:
                result = self._terminal_result(board)
                terminal_provenance = Provenance(
                    normalized_fen=board.fen(en_passant="fen"),
                    engine_build="terminal/no-search",
                    network_hash=None,
                    options={},
                    selected_depth=None,
                    raw_scores=[],
                    original_pv_lengths=[],
                    elapsed_attempt_ms=int((time.monotonic() - started) * 1000),
                    elapsed_search_ms=0,
                    elapsed_replay_ms=0,
                    elapsed_render_ms=0,
                    incomplete_groups=0,
                    bound_only_groups=0,
                    duplicate_root_groups=0,
                    inconsistent_groups=0,
                )
                candidate_bundle = build_candidate_result(board, None, terminal_provenance, requested_count=int(_value(config, "candidate_target", "requested_count", default=3)), search_budget_ms=int(search_time_seconds * 1000), max_continuation_plies=int(_value(config, "max_continuation_plies", "continuation_cap", default=6)))
            else:
                remaining_hard_timeout = absolute_deadline - time.monotonic()
                if remaining_hard_timeout <= search_time_seconds:
                    raise EngineTimeout("engine evaluation exceeded the remaining attempt budget")
                result, candidate_bundle = self._candidate_attempt(board, config, started, absolute_deadline)
            if time.monotonic() >= absolute_deadline:
                raise EngineTimeout("candidate processing exceeded attempt budget")
        except Exception as exc:
            code = getattr(exc, "code", "ENGINE_ERROR")
            self.log.warning(
                "worker transition",
                extra={
                    "task_id": str(task_id),
                    "attempt": attempts,
                    "transition": "evaluation_failed",
                    "failure_code": code,
                    "elapsed": time.monotonic() - started,
                    "factual_elapsed": factual_elapsed,
                    "factual_version": FACTUAL_RESULT_VERSION,
                },
            )
            try:
                self._persist_failure(task, exc, started, retryable=code not in NON_RETRYABLE_FAILURE_CODES)
                self.log.info(
                    "worker transition",
                    extra={
                        "task_id": str(task_id),
                        "attempt": attempts,
                        "transition": "retry_or_failed",
                        "failure_code": code,
                        "elapsed": time.monotonic() - started,
                        "factual_elapsed": factual_elapsed,
                        "factual_version": FACTUAL_RESULT_VERSION,
                    },
                )
            except Exception:
                self._db_blocked = True
                self.log.exception(
                    "could not persist worker failure",
                    extra={
                        "task_id": str(task_id),
                        "attempt": attempts,
                        "transition": "persistence_failed",
                        "failure_code": "DB_UNAVAILABLE",
                        "elapsed": time.monotonic() - started,
                        "factual_elapsed": factual_elapsed,
                        "factual_version": FACTUAL_RESULT_VERSION,
                    },
                )
                raise
            return True
        # A final guard closes the race between validation/model serialization
        # and persistence.  An expired attempt must enter the normal retry or
        # terminal-failure path, never publish a completed result.
        if time.monotonic() >= absolute_deadline:
            timeout = EngineTimeout("attempt deadline expired before persistence")
            try:
                self._persist_failure(task, timeout, started, retryable=True)
            except Exception:
                self._db_blocked = True
                self.log.exception(
                    "could not persist deadline failure",
                    extra={
                        "task_id": str(task_id),
                        "attempt": attempts,
                        "transition": "persistence_failed",
                        "failure_code": "DB_UNAVAILABLE",
                        "elapsed": time.monotonic() - started,
                        "factual_elapsed": factual_elapsed,
                        "factual_version": FACTUAL_RESULT_VERSION,
                    },
                )
                raise
            return True
        try:
            self.engine_version = candidate_bundle.provenance.engine_build
            if self._persist_success(task, result, factual_bundle.model_dump(mode="json"), started, candidate_bundle.model_dump(mode="json", by_alias=True)):
                self.log.info(
                    "worker transition",
                    extra={
                        "task_id": str(task_id),
                        "attempt": attempts,
                        "transition": "completed",
                        "elapsed": time.monotonic() - started,
                        "factual_elapsed": factual_elapsed,
                        "factual_version": FACTUAL_RESULT_VERSION,
                    },
                )
        except Exception:
            # Keep persistence errors distinct from engine failures.  The
            # guarded lease will make this attempt recoverable later.
            self._db_blocked = True
            self.log.exception(
                "could not persist worker result",
                extra={
                    "task_id": str(task_id),
                    "attempt": attempts,
                    "transition": "persistence_failed",
                    "failure_code": "DB_UNAVAILABLE",
                    "elapsed": time.monotonic() - started,
                    "factual_elapsed": factual_elapsed,
                    "factual_version": FACTUAL_RESULT_VERSION,
                },
            )
            raise
        return True

    def _install_signal_handlers(self) -> None:
        if self._handlers_installed:
            return
        try:
            signal.signal(signal.SIGTERM, lambda _signum, _frame: self.stop_event.set())
            signal.signal(signal.SIGINT, lambda _signum, _frame: self.stop_event.set())
            self._handlers_installed = True
        except (ValueError, RuntimeError):
            # Non-main-thread workers use an injected Event instead.
            pass

    def run_forever(self, config: Any = None) -> None:
        c = validate_startup_config(
            config if config is not None else self.evaluation_config,
            check_engine=self.evaluator is evaluate_fen and self.candidate_evaluator is None,
        )
        self.evaluation_config = config if config is not None else self.evaluation_config
        # Engine identity is carried by the same candidate-search attempt;
        # do not spawn a separate discovery process at worker startup.
        self.engine_version = str(_value(config, "engine_version", default=None) or "custom")
        interval = self._poll_interval if self._poll_interval is not None else c.poll_interval
        self._install_signal_handlers()
        self.log.info("worker started", extra={"transition": "started", "poll_interval": interval, "engine_version": self.engine_version})
        while not self.stop_event.is_set():
            try:
                self.process_once()
            except Exception:
                self.log.exception("worker poll failed", extra={"transition": "poll_failed", "failure_code": "DB_UNAVAILABLE"})
            if not self.stop_event.is_set():
                self.stop_event.wait(interval)
        self.log.info("worker stopped", extra={"transition": "stopped"})


def run_worker(config: Any = None, **kwargs: Any) -> None:
    """Entrypoint used by the worker container/process."""
    Worker(evaluation_config=config, **kwargs).run_forever(config)


def main() -> None:
    """Container entrypoint; configuration is loaded only in the worker process."""
    from .config import get_settings

    configure_logging()
    run_worker(get_settings())


if __name__ == "__main__":  # pragma: no cover - exercised by the container
    main()


__all__ = [
    "JsonLogFormatter",
    "Worker",
    "WorkerConfig",
    "WorkerConfigError",
    "coerce_config",
    "configure_logging",
    "main",
    "run_worker",
    "validate_startup_config",
]
