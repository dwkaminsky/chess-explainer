"""Small, bounded Stockfish adapter used by the worker.

The adapter deliberately has a narrow contract: :func:`evaluate_fen` returns
an integer centipawn score from White's point of view, or a :class:`MateResult`.
It raises an :class:`EngineError` for every unusable engine outcome; in
particular, a missing score is never silently changed to zero.

The worker owns the deployment configuration.  This module accepts either a
mapping or a settings object and recognises the names used by the MVP config
(``engine_path``, ``search_time``, ``hard_timeout``, ``threads``, and
``hash_mb``).  A new ``python-chess`` ``SimpleEngine`` is opened for every
call.  The evaluation itself runs in a daemon watchdog thread so a wedged UCI
call cannot wedge the worker; the engine is closed/terminated before the call
returns when possible.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

try:  # Imported lazily by tests in environments without the runtime package.
    import chess
    import chess.engine
except ImportError:  # pragma: no cover - exercised only by dependency checks
    chess = None  # type: ignore[assignment]

from .constants import ENGINE_CLEANUP_MARGIN


DEFAULT_ENGINE_PATH = "stockfish"
DEFAULT_SEARCH_TIME = 3.0
DEFAULT_HARD_TIMEOUT = 10.0
DEFAULT_THREADS = 1
DEFAULT_HASH_MB = 64
DEFAULT_SKILL = 20
WATCHDOG_CLEANUP_MARGIN = ENGINE_CLEANUP_MARGIN

# A timed-out Python thread cannot be forcefully killed.  Keep the attempt
# registered until its target returns so a retry cannot start a second engine
# while a pathological adapter is still unwinding.
_active_attempt_guard = threading.Lock()
_active_attempt: threading.Thread | None = None


def _register_attempt(thread: threading.Thread) -> None:
    global _active_attempt
    with _active_attempt_guard:
        if _active_attempt is not None:
            raise EngineTimeout("a previous engine attempt is still shutting down")
        _active_attempt = thread


def _release_attempt(thread: threading.Thread | None = None) -> None:
    global _active_attempt
    with _active_attempt_guard:
        expected = thread or threading.current_thread()
        if _active_attempt is expected:
            _active_attempt = None


@dataclass(frozen=True)
class MateResult:
    """A mate score, kept separate from an ordinary centipawn score."""

    winner: str
    moves: int

    def __post_init__(self) -> None:
        if self.winner not in {"white", "black"}:
            raise ValueError("mate winner must be 'white' or 'black'")
        if self.moves < 0:
            raise ValueError("mate distance must be non-negative")

    @property
    def mate_winner(self) -> str:
        return self.winner

    @property
    def mate_moves(self) -> int:
        return self.moves


class EngineError(RuntimeError):
    """Base class for failures that should be retried by the worker."""

    code = "ENGINE_ERROR"


class EngineTimeout(EngineError):
    code = "ENGINE_TIMEOUT"


class EngineCrashed(EngineError):
    code = "ENGINE_CRASH"


class MissingScore(EngineError):
    code = "ENGINE_SCORE_MISSING"


class InvalidEngineConfig(EngineError):
    code = "ENGINE_CONFIG_INVALID"


@dataclass(frozen=True)
class CandidateSearchResult:
    snapshot: "CandidateSnapshot | None"
    engine_build: str
    network_hash: str | None
    options: dict[str, Any]
    elapsed_search_ms: int
    diagnostics: "CollectorDiagnostics"
    terminal: bool = False


def _value(config: Any, *names: str, default: Any = None) -> Any:
    if config is None:
        return default
    for name in names:
        if isinstance(config, Mapping) and name in config:
            return config[name]
        if hasattr(config, name):
            return getattr(config, name)
    return default


def _engine_path(config: Any) -> str:
    path = _value(config, "engine_path", "stockfish_path", "path", default=DEFAULT_ENGINE_PATH)
    return os.fspath(path)


def _settings(config: Any) -> tuple[str, float, float, int, int, int]:
    path = _engine_path(config)
    search = float(_value(config, "search_time", "engine_time", "time_limit", "engine_time_limit", "search_time_seconds", default=DEFAULT_SEARCH_TIME))
    hard = float(_value(config, "hard_timeout", "attempt_timeout", "hard_attempt_timeout", "hard_attempt_timeout_seconds", default=DEFAULT_HARD_TIMEOUT))
    threads = int(_value(config, "threads", "engine_threads", default=DEFAULT_THREADS))
    hash_mb = int(_value(config, "hash_mb", "hash_memory_mb", "hash", "engine_hash_mb", default=DEFAULT_HASH_MB))
    skill = int(_value(config, "skill", "skill_level", default=DEFAULT_SKILL))
    if search <= 0 or hard <= 0 or hard - search < ENGINE_CLEANUP_MARGIN or threads != 1 or hash_mb != 64 or not 0 <= skill <= 20:
        raise InvalidEngineConfig("hard timeout must exceed search time by at least the cleanup margin; fixed Stockfish resources are Threads=1 and Hash=64")
    return path, search, hard, threads, hash_mb, skill


def _require_chess() -> Any:
    if chess is None:
        raise EngineCrashed("python-chess is not installed")
    return chess


def _terminal_result(board: Any) -> int | MateResult | None:
    """Return a result known from the supplied FEN, without inventing history."""
    if board.is_checkmate():
        # ``turn`` is the side that has just been mated.
        return MateResult("white" if board.turn == chess.BLACK else "black", 0)
    # These outcomes are intrinsic to the board and don't require repetition
    # history.  ``is_fifty_moves`` and ``can_claim_*`` are intentionally not
    # used: a FEN cannot prove the prior history needed for a claim.
    if (
        board.is_stalemate()
        or board.is_insufficient_material()
        or getattr(board, "is_seventyfive_moves", lambda: False)()
        or getattr(board, "is_variant_draw", lambda: False)()
    ):
        return 0
    if getattr(board, "is_variant_loss", lambda: False)():
        return MateResult("black" if board.turn == chess.WHITE else "white", 0)
    if getattr(board, "is_variant_win", lambda: False)():
        return MateResult("white" if board.turn == chess.WHITE else "black", 0)
    return None


def _engine_build_from_identity(engine: Any, path: str) -> str:
    identity = getattr(engine, "id", {}) or {}
    if isinstance(identity, Mapping):
        name = identity.get("name")
        author = identity.get("author")
        if name and author:
            return f"{name} ({author})"
        if name:
            return str(name)
    return os.path.basename(path) or path


def _network_hash_from_engine(engine: Any | None, config: Any) -> str | None:
    value = _value(config, "network_hash", "engine_network_hash", "nnue_hash", "engine_nnue_hash")
    if value is not None:
        return str(value)
    if engine is None:
        return None
    protocol = getattr(engine, "protocol", None)
    target_config = getattr(protocol, "target_config", None)
    options = getattr(engine, "options", {}) or {}
    candidate_keys = ("EvalFile", "EvalFileName", "NNUEFile", "NetworkFile")
    for key in candidate_keys:
        for source in (target_config, options):
            if not isinstance(source, Mapping) or key not in source:
                continue
            option = source.get(key)
            if option is None:
                continue
            if hasattr(option, "default"):
                option = getattr(option, "default")
            if option is None:
                continue
            try:
                path = os.fspath(option)
            except TypeError:
                path = str(option)
            if path:
                return os.path.basename(path) or path
    return None


def _candidate_options(threads: int, hash_mb: int, skill: int, multipv: int) -> dict[str, Any]:
    return {
        "Threads": threads,
        "Hash": hash_mb,
        "Skill Level": skill,
        "UCI_LimitStrength": False,
        "MultiPV": multipv,
    }


def _board_from_input(board_or_fen: Any) -> Any:
    if chess is None:
        raise EngineCrashed("python-chess is not installed")
    if isinstance(board_or_fen, chess.Board):
        return board_or_fen.copy(stack=True)
    if isinstance(board_or_fen, str):
        try:
            return chess.Board(board_or_fen)
        except Exception as exc:
            raise InvalidEngineConfig("invalid FEN supplied to engine adapter") from exc
    raise InvalidEngineConfig("board_or_fen must be a FEN string or chess.Board")


def _is_bound(score: Any, info: Mapping[str, Any]) -> bool:
    if info.get("lowerbound") or info.get("upperbound"):
        return True
    def flag(name: str) -> bool:
        value = getattr(score, name, False)
        return bool(value() if callable(value) else value)
    return flag("is_lowerbound") or flag("is_upperbound")


def _normalise_score(info: Mapping[str, Any]) -> int | MateResult:
    score = info.get("score")
    if score is None:
        raise MissingScore("engine returned no score")
    if _is_bound(score, info):
        raise MissingScore("engine returned only a bounded score")
    # python-chess's PovScore is the canonical place to normalize perspective.
    try:
        score = score.pov(chess.WHITE)
        mate = score.mate()
    except (AttributeError, TypeError) as exc:
        raise MissingScore("engine returned an invalid score") from exc
    if mate is not None:
        # A positive mate distance means White mates; negative means Black.
        try:
            return MateResult("white" if mate > 0 else "black", abs(int(mate)))
        except (TypeError, ValueError) as exc:
            raise MissingScore("engine returned an invalid mate score") from exc
    try:
        cp = score.score(mate_score=None)
    except (AttributeError, TypeError) as exc:
        raise MissingScore("engine returned an invalid centipawn score") from exc
    if cp is None:
        raise MissingScore("engine returned no centipawn score")
    try:
        return int(cp)
    except (TypeError, ValueError) as exc:
        raise MissingScore("engine returned an invalid centipawn score") from exc


# Both spellings are useful to callers and make the normalization policy easy
# to unit-test independently of process management.
normalize_score = _normalise_score
normalise_score = _normalise_score


def _engine_process(engine: Any) -> Any | None:
    """Find the subprocess owned by a python-chess engine, if exposed.

    python-chess has used both ``_proc`` and ``proc`` on its asyncio transport
    across releases.  Keeping this lookup defensive also makes the watchdog
    usable with small test doubles.
    """
    if engine is None:
        return None
    transports = [
        getattr(getattr(engine, "protocol", None), "transport", None),
        getattr(engine, "transport", None),
    ]
    for transport in transports:
        if transport is None:
            continue
        for name in ("_proc", "proc", "process"):
            process = getattr(transport, name, None)
            if process is not None:
                return process
        # Some doubles expose the process directly as the transport.
        if any(hasattr(transport, name) for name in ("terminate", "kill", "poll", "returncode")):
            return transport
    for name in ("_proc", "proc", "process"):
        process = getattr(engine, name, None)
        if process is not None:
            return process
    return None


def _process_returncode(process: Any) -> Any:
    value = getattr(process, "returncode", None)
    if callable(value):
        try:
            return value()
        except Exception:
            return None
    poll = getattr(process, "poll", None)
    if callable(poll):
        try:
            return poll()
        except Exception:
            return None
    return value


def _wait_process(process: Any, timeout: float, engine: Any) -> bool:
    """Wait for either a normal or asyncio subprocess to be reaped."""
    if process is None:
        return True
    if _process_returncode(process) is not None:
        return True
    waiter = getattr(process, "wait", None)
    if not callable(waiter):
        return _process_returncode(process) is not None
    try:
        result = waiter(timeout=timeout)
    except TypeError:
        # ``asyncio.subprocess.Process.wait`` has no timeout parameter, and a
        # hostile synchronous double may block forever.  Never call that form
        # on the watchdog thread directly.
        wait_done = threading.Event()
        wait_result: dict[str, Any] = {}

        def wait_without_timeout() -> None:
            try:
                wait_result["value"] = waiter()
            except Exception as exc:
                wait_result["error"] = exc
            finally:
                wait_done.set()

        threading.Thread(target=wait_without_timeout, name="stockfish-wait", daemon=True).start()
        if not wait_done.wait(timeout):
            return False
        if "error" in wait_result:
            return _process_returncode(process) is not None
        result = wait_result.get("value")
    except Exception:
        return _process_returncode(process) is not None
    if inspect.isawaitable(result):
        loop = getattr(getattr(engine, "protocol", None), "loop", None)
        if loop is None or not loop.is_running():
            return False
        try:
            awaited_result = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(result, timeout=max(0.0, timeout)), loop
            ).result(max(0.0, timeout))
        except Exception:
            return False
        return awaited_result is not None or _process_returncode(process) is not None
    if result is not None:
        return True
    return _process_returncode(process) is not None


def _close_engine(engine: Any, grace: float = 0.25) -> bool:
    """Stop and reap an engine before returning from an attempt.

    ``SimpleEngine.quit()`` can wait for a wedged UCI call.  ``close()`` is
    therefore requested first when available, and the subprocess is then
    terminated/killed directly.  The caller joins its analysis thread after
    this function so a timeout cannot leave a live engine worker behind.
    """
    if engine is None:
        return True
    grace = max(0.0, grace)
    cleanup_deadline = time.monotonic() + grace

    def remaining() -> float:
        return max(0.0, cleanup_deadline - time.monotonic())

    close_completed = True
    close = getattr(engine, "close", None)
    if callable(close):
        close_done = threading.Event()

        def close_engine() -> None:
            try:
                close()
            except Exception:
                pass
            finally:
                close_done.set()

        threading.Thread(target=close_engine, name="stockfish-close", daemon=True).start()
        close_done.wait(min(remaining(), 0.1))
        close_completed = close_done.is_set()
    else:
        quit_method = getattr(engine, "quit", None)
        if callable(quit_method):
            # A wedged fake/UCI implementation must not make watchdog cleanup
            # unbounded.  The subprocess kill below is the authoritative stop.
            quit_done = threading.Event()

            def quit() -> None:
                try:
                    quit_method()
                except Exception:
                    pass
                finally:
                    quit_done.set()

            threading.Thread(target=quit, name="stockfish-quit", daemon=True).start()
            quit_done.wait(min(remaining(), 0.1))
            close_completed = quit_done.is_set()

    process = _engine_process(engine)
    if process is None:
        return close_completed
    if _process_returncode(process) is None:
        try:
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                terminate()
        except Exception:
            pass
        if not _wait_process(process, remaining(), engine) and _process_returncode(process) is None:
            try:
                kill = getattr(process, "kill", None)
                if callable(kill):
                    kill()
            except Exception:
                pass
            _wait_process(process, remaining(), engine)
    return close_completed and _process_returncode(process) is not None


def _evaluate_engine(fen: str, config: Any, holder: dict[str, Any], output: dict[str, Any]) -> None:
    engine = None
    try:
        path, search, _hard, threads, hash_mb, skill = _settings(config)
        remaining = max(0.01, holder["deadline"] - time.monotonic())
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=remaining)
        holder["engine"] = engine
        options = {"Threads": threads, "Hash": hash_mb, "Skill Level": skill}
        # Full strength means the maximum skill level and no artificial limit.
        options["UCI_LimitStrength"] = False
        engine.configure(options)
        remaining = holder["deadline"] - time.monotonic()
        if remaining <= 0:
            raise EngineTimeout("engine startup exceeded hard attempt limit")
        board = chess.Board(fen)
        info = engine.analyse(board, chess.engine.Limit(time=min(search, remaining)), multipv=1)
        if isinstance(info, list):
            info = info[0] if info else {}
        output["value"] = _normalise_score(info)
    except EngineError as exc:
        output["error"] = exc
    except TimeoutError as exc:
        output["error"] = EngineTimeout(str(exc) or "engine evaluation timed out")
    except Exception as exc:
        output["error"] = EngineCrashed(str(exc) or "Stockfish failed")
    finally:
        if holder.get("cleanup_done") and holder.get("cleanup_ok"):
            _release_attempt()


def _analysis_stream(analysis: Any):
    while True:
        info = analysis.next()
        if info is None:
            return
        yield info


def _candidate_target(config: Any, legal_root_moves: int) -> int:
    target = int(_value(config, "candidate_target", "requested_count", default=3))
    if target < 1 or target > 3:
        raise InvalidEngineConfig("candidate target must be between one and three")
    return min(target, legal_root_moves)


def _evaluate_candidates_thread(
    board: Any,
    config: Any,
    holder: dict[str, Any],
    output: dict[str, Any],
) -> None:
    engine = None
    try:
        from app.candidates.collect import CandidateCollectError, IncompleteCandidateSet, collect_reports_with_diagnostics

        path, search, _hard, threads, hash_mb, skill = _settings(config)
        target_count = _candidate_target(config, sum(1 for _ in board.legal_moves))
        remaining = max(0.01, holder["deadline"] - time.monotonic())
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=remaining)
        holder["engine"] = engine
        options = _candidate_options(threads, hash_mb, skill, target_count)
        engine.configure({k: v for k, v in options.items() if k != "MultiPV"})
        remaining = holder["deadline"] - time.monotonic()
        if remaining <= 0:
            raise EngineTimeout("engine startup exceeded hard attempt limit")
        search_started = time.monotonic()
        with engine.analysis(
            board,
            chess.engine.Limit(time=min(search, remaining)),
            multipv=target_count,
            info=chess.engine.Info.ALL,
        ) as analysis:
            collected = collect_reports_with_diagnostics(_analysis_stream(analysis), board, target_count)
        if collected.snapshot is None:
            raise IncompleteCandidateSet(diagnostics=collected.diagnostics)
        output["value"] = CandidateSearchResult(
            snapshot=collected.snapshot,
            engine_build=_engine_build_from_identity(engine, path),
            network_hash=_network_hash_from_engine(engine, config),
            options=options,
            elapsed_search_ms=int(round((time.monotonic() - search_started) * 1000)),
            diagnostics=collected.diagnostics,
            terminal=False,
        )
    except (IncompleteCandidateSet, CandidateCollectError) as exc:
        output["error"] = exc
    except EngineError as exc:
        output["error"] = exc
    except TimeoutError as exc:
        output["error"] = EngineTimeout(str(exc) or "engine evaluation timed out")
    except Exception as exc:
        output["error"] = EngineCrashed(str(exc) or "Stockfish failed")
    finally:
        if holder.get("cleanup_done") and holder.get("cleanup_ok"):
            _release_attempt()


def evaluate_terminal(board_or_fen: Any, evaluation_config: Any = None) -> CandidateSearchResult:
    board = _board_from_input(board_or_fen)
    terminal = _terminal_result(board)
    if terminal is None:
        raise InvalidEngineConfig("evaluate_terminal only handles terminal roots")
    from app.candidates.collect import CollectorDiagnostics

    return CandidateSearchResult(
        snapshot=None,
        engine_build="terminal/no-search",
        network_hash=_network_hash_from_engine(None, evaluation_config),
        options={},
        elapsed_search_ms=0,
        diagnostics=CollectorDiagnostics(),
        terminal=True,
    )


def evaluate_candidates(board_or_fen: Any, evaluation_config: Any = None) -> CandidateSearchResult:
    board = _board_from_input(board_or_fen)
    if _terminal_result(board) is not None:
        return evaluate_terminal(board, evaluation_config)

    _, _, hard_timeout, _, _, _ = _settings(evaluation_config)
    holder: dict[str, Any] = {"engine": None, "deadline": time.monotonic() + hard_timeout}
    output: dict[str, Any] = {}
    thread = threading.Thread(
        target=_evaluate_candidates_thread,
        args=(board, evaluation_config, holder, output),
        name="stockfish-candidates",
        daemon=True,
    )
    _register_attempt(thread)
    thread.start()
    # Reserve time for killing/reaping the engine and joining the analysis
    # thread.  A timed join followed by a return would leak a live worker.
    thread.join(max(0.0, holder["deadline"] - time.monotonic() - WATCHDOG_CLEANUP_MARGIN))
    holder["cleanup_ok"] = _close_engine(holder.get("engine"), grace=max(0.0, holder["deadline"] - time.monotonic()))
    thread.join(max(0.0, holder["deadline"] - time.monotonic()))
    holder["cleanup_done"] = True
    if thread.is_alive() or not holder["cleanup_ok"]:
        raise EngineTimeout("engine evaluation exceeded hard attempt limit")
    _release_attempt(thread)
    if "error" in output:
        raise output["error"]
    if "value" not in output:
        raise EngineCrashed("Stockfish failed to return a candidate snapshot")
    return output["value"]


def evaluate_fen(fen: str, evaluation_config: Any = None) -> int | MateResult:
    """Evaluate one validated FEN with a fresh bounded Stockfish process.

    ``fen`` is reparsed defensively.  Callers should treat ``EngineError`` as
    retryable.  The hard cap includes startup, configuration, search, and
    shutdown; it is independent of the requested search time.
    """
    c = _require_chess()
    try:
        board = c.Board(fen)
    except Exception as exc:
        raise InvalidEngineConfig("invalid FEN supplied to engine adapter") from exc
    terminal = _terminal_result(board)
    if terminal is not None:
        return terminal
    _settings(evaluation_config)  # Validate before starting a child.
    holder: dict[str, Any] = {"engine": None, "deadline": time.monotonic() + _settings(evaluation_config)[2]}
    output: dict[str, Any] = {}
    thread = threading.Thread(target=_evaluate_engine, args=(fen, evaluation_config, holder, output), name="stockfish-attempt", daemon=True)
    _register_attempt(thread)
    thread.start()
    thread.join(max(0.0, holder["deadline"] - time.monotonic() - WATCHDOG_CLEANUP_MARGIN))
    holder["cleanup_ok"] = _close_engine(holder.get("engine"), grace=max(0.0, holder["deadline"] - time.monotonic()))
    thread.join(max(0.0, holder["deadline"] - time.monotonic()))
    holder["cleanup_done"] = True
    if thread.is_alive() or not holder["cleanup_ok"]:
        raise EngineTimeout("engine evaluation exceeded hard attempt limit")
    _release_attempt(thread)
    if "error" in output:
        raise output["error"]
    if "value" not in output:
        raise MissingScore("engine returned no usable result")
    return output["value"]


def engine_identity(evaluation_config: Any = None) -> str:
    """Return the UCI engine identity, used as the persisted result metadata."""
    _require_chess()
    path, _search, hard, _threads, _hash, _skill = _settings(evaluation_config)
    engine = None
    try:
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=min(hard, 5.0))
        return _engine_build_from_identity(engine, path)
    finally:
        _close_engine(engine)


__all__ = [
    "CandidateSearchResult",
    "EngineCrashed",
    "EngineError",
    "EngineTimeout",
    "InvalidEngineConfig",
    "MateResult",
    "MissingScore",
    "engine_identity",
    "evaluate_candidates",
    "evaluate_fen",
    "evaluate_terminal",
    "normalise_score",
    "normalize_score",
]
