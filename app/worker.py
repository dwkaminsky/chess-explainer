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

from .facts.extract import (
    ExplanationRenderError,
    FactExtractionError,
    FactualResultValidationError,
    build_factual_result,
)
from .stockfish import EngineError, MateResult, engine_identity, evaluate_fen

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerConfig:
    engine_path: str = "stockfish"
    search_time: float = 1.0
    hard_timeout: float = 10.0
    threads: int = 1
    hash_mb: int = 64
    skill: int = 20
    poll_interval: float = 0.5
    lease_seconds: float = 30.0
    max_attempts: int = 2


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
}

NON_RETRYABLE_FAILURE_CODES = {
    "ENGINE_CONFIG_INVALID",
    "FACT_EXTRACTION_FAILED",
    "EXPLANATION_RENDER_FAILED",
    "FACTUAL_RESULT_INVALID",
}


class JsonLogFormatter(logging.Formatter):
    """Compact JSON logs retaining worker transition context."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("task_id", "attempt", "transition", "elapsed", "failure_code", "engine_version", "poll_interval"):
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
        search_time=float(_value(config, "search_time", "engine_time", "time_limit", "engine_time_limit", "engine_time_seconds", "search_time_seconds", default=1.0)),
        hard_timeout=float(_value(config, "hard_timeout", "attempt_timeout", "hard_attempt_timeout", "engine_hard_timeout", "hard_timeout_seconds", "hard_attempt_timeout_seconds", default=10.0)),
        threads=int(_value(config, "threads", "engine_threads", "stockfish_threads", default=1)),
        hash_mb=int(_value(config, "hash_mb", "hash_memory_mb", "hash", "stockfish_hash_mb", "engine_hash_mb", default=64)),
        skill=int(_value(config, "skill", "skill_level", default=20)),
        poll_interval=float(_value(config, "poll_interval", "worker_poll_interval", "poll_interval_seconds", default=0.5)),
        lease_seconds=float(_value(config, "lease_seconds", "task_lease_seconds", default=30.0)),
        max_attempts=int(_value(config, "max_attempts", "worker_max_attempts", default=2)),
    )


def validate_startup_config(config: Any = None, *, check_engine: bool = True) -> WorkerConfig:
    """Validate deployment settings before claiming any task."""
    c = coerce_config(config)
    if c.search_time <= 0 or c.hard_timeout <= 0 or c.search_time > c.hard_timeout:
        raise WorkerConfigError("search time must be positive and no greater than hard timeout")
    if c.hard_timeout >= c.lease_seconds:
        raise WorkerConfigError("hard timeout must be shorter than the task lease")
    if c.threads != 1:
        raise WorkerConfigError("MVP worker requires exactly one engine thread")
    if c.hash_mb < 1 or not 0 <= c.skill <= 20:
        raise WorkerConfigError("invalid fixed Stockfish settings")
    if c.poll_interval <= 0 or c.max_attempts != 2:
        raise WorkerConfigError("MVP polling/attempt settings are fixed at 500ms/two attempts")
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
        task_api: Any = None,
        poll_interval: float | None = None,
        stop_event: threading.Event | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.evaluation_config = evaluation_config
        self.evaluator = evaluator
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
        self.log.info("worker transition", extra={"task_id": str(task_id), "attempt": attempts, "transition": "claimed", "elapsed": 0.0})
        try:
            board = chess.Board(fen)
            factual_bundle = build_factual_result(board)
        except (FactExtractionError, ExplanationRenderError, FactualResultValidationError) as exc:
            code = getattr(exc, "code", "FACT_EXTRACTION_FAILED")
            self.log.warning("worker transition", extra={"task_id": str(task_id), "attempt": attempts, "transition": "factual_generation_failed", "failure_code": code, "elapsed": time.monotonic() - started})
            try:
                self._persist_failure(task, exc, started, retryable=False)
                self.log.info("worker transition", extra={"task_id": str(task_id), "attempt": attempts, "transition": "failed", "failure_code": code, "elapsed": time.monotonic() - started})
            except Exception:
                self._db_blocked = True
                self.log.exception("could not persist factual failure", extra={"task_id": str(task_id), "attempt": attempts, "transition": "persistence_failed", "failure_code": "DB_UNAVAILABLE", "elapsed": time.monotonic() - started})
                raise
            return True
        except Exception as exc:
            code = getattr(exc, "code", "FACT_EXTRACTION_FAILED")
            self.log.warning("worker transition", extra={"task_id": str(task_id), "attempt": attempts, "transition": "factual_generation_failed", "failure_code": code, "elapsed": time.monotonic() - started})
            try:
                self._persist_failure(task, exc, started, retryable=False)
                self.log.info("worker transition", extra={"task_id": str(task_id), "attempt": attempts, "transition": "failed", "failure_code": code, "elapsed": time.monotonic() - started})
            except Exception:
                self._db_blocked = True
                self.log.exception("could not persist factual failure", extra={"task_id": str(task_id), "attempt": attempts, "transition": "persistence_failed", "failure_code": "DB_UNAVAILABLE", "elapsed": time.monotonic() - started})
                raise
            return True
        try:
            result = self.evaluator(fen, config)
            if not isinstance(result, (int, MateResult)):
                raise EngineError("evaluator returned an unusable result")
        except Exception as exc:
            code = getattr(exc, "code", "ENGINE_ERROR")
            self.log.warning("worker transition", extra={"task_id": str(task_id), "attempt": attempts, "transition": "evaluation_failed", "failure_code": code, "elapsed": time.monotonic() - started})
            try:
                self._persist_failure(task, exc, started, retryable=code not in NON_RETRYABLE_FAILURE_CODES)
                self.log.info("worker transition", extra={"task_id": str(task_id), "attempt": attempts, "transition": "retry_or_failed", "failure_code": code, "elapsed": time.monotonic() - started})
            except Exception:
                self._db_blocked = True
                self.log.exception("could not persist worker failure", extra={"task_id": str(task_id), "attempt": attempts, "transition": "persistence_failed", "failure_code": "DB_UNAVAILABLE", "elapsed": time.monotonic() - started})
                raise
            return True
        try:
            if self._persist_success(task, result, factual_bundle.model_dump(mode="json"), started):
                self.log.info("worker transition", extra={"task_id": str(task_id), "attempt": attempts, "transition": "completed", "elapsed": time.monotonic() - started})
        except Exception:
            # Keep persistence errors distinct from engine failures.  The
            # guarded lease will make this attempt recoverable later.
            self._db_blocked = True
            self.log.exception("could not persist worker result", extra={"task_id": str(task_id), "attempt": attempts, "transition": "persistence_failed", "failure_code": "DB_UNAVAILABLE", "elapsed": time.monotonic() - started})
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
        c = validate_startup_config(config if config is not None else self.evaluation_config, check_engine=self.evaluator is evaluate_fen)
        self.evaluation_config = config if config is not None else self.evaluation_config
        self.engine_version = engine_identity(self.evaluation_config or c) if self.evaluator is evaluate_fen else _value(config, "engine_version", default="custom")
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
