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


DEFAULT_ENGINE_PATH = "stockfish"
DEFAULT_SEARCH_TIME = 1.0
DEFAULT_HARD_TIMEOUT = 10.0
DEFAULT_THREADS = 1
DEFAULT_HASH_MB = 64
DEFAULT_SKILL = 20


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
    if search <= 0 or hard <= 0 or search > hard or threads < 1 or hash_mb < 1 or not 0 <= skill <= 20:
        raise InvalidEngineConfig("invalid Stockfish evaluation settings")
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


def _close_engine(engine: Any, grace: float = 0.25) -> None:
    """Close an engine without allowing cleanup to defeat the watchdog."""
    if engine is None:
        return
    done = threading.Event()

    def close() -> None:
        try:
            # quit() is the public python-chess API and gives UCI a chance to
            # exit cleanly.  close() is useful for test doubles and older APIs.
            quitter = getattr(engine, "quit", None) or getattr(engine, "close", None)
            if quitter:
                quitter()
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=close, name="stockfish-cleanup", daemon=True).start()
    done.wait(max(0.01, grace))
    if done.is_set():
        return
    # SimpleEngine's transport ultimately owns a subprocess.  This fallback
    # is intentionally defensive because it varies between python-chess
    # releases; the public close path above remains the normal path.
    candidates = [
        getattr(getattr(engine, "protocol", None), "transport", None),
        getattr(engine, "transport", None),
    ]
    for transport in candidates:
        proc = getattr(transport, "_proc", None) or getattr(transport, "proc", None) or getattr(transport, "process", None)
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=grace)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


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
        _close_engine(engine, grace=min(0.25, max(0.01, holder["deadline"] - time.monotonic())))


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
    thread.start()
    thread.join(max(0.01, holder["deadline"] - time.monotonic()))
    if thread.is_alive():
        _close_engine(holder.get("engine"), grace=0.25)
        raise EngineTimeout("engine evaluation exceeded hard attempt limit")
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
        identity = getattr(engine, "id", {}) or {}
        name = identity.get("name") if isinstance(identity, Mapping) else None
        author = identity.get("author") if isinstance(identity, Mapping) else None
        if name and author:
            return f"{name} ({author})"
        if name:
            return str(name)
        return os.path.basename(path) or path
    finally:
        _close_engine(engine)


__all__ = [
    "EngineCrashed",
    "EngineError",
    "EngineTimeout",
    "InvalidEngineConfig",
    "MateResult",
    "MissingScore",
    "engine_identity",
    "evaluate_fen",
    "normalise_score",
    "normalize_score",
]
