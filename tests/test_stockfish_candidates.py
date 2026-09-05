from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

chess = pytest.importorskip("chess")

from app import stockfish
from app.candidates.collect import IncompleteCandidateSet, collect_reports, collect_reports_with_diagnostics
from app.candidates.replay import replay_candidate
from app.candidates.scores import normalize_and_compare, validate_rank_order


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "authored_stockfish_candidate_transcript.json"


@pytest.fixture(autouse=True)
def reset_attempt_guard():
    """Keep the module-level poisoned-attempt state isolated per test."""
    yield
    with stockfish._active_attempt_guard:
        stockfish._active_attempt = None
CAPTURED_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "pinned_stockfish_15_1_candidate_transcript.json"


def _load_transcript() -> list[dict[str, object]]:
    return json.loads(FIXTURE_PATH.read_text())


def _load_captured_transcript() -> dict[str, object]:
    return json.loads(CAPTURED_FIXTURE_PATH.read_text())


class FakeAnalysis:
    def __init__(self, packets: list[dict[str, object]], *, next_error: Exception | None = None):
        self._packets = [copy.deepcopy(packet) for packet in packets]
        self._index = 0
        self._next_error = next_error
        self.stop_calls = 0
        self.wait_calls = 0

    def __enter__(self) -> "FakeAnalysis":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        self.wait()
        return False

    def next(self):
        if self._next_error is not None and self._index == 0:
            raise self._next_error
        if self._index >= len(self._packets):
            return None
        packet = copy.deepcopy(self._packets[self._index])
        self._index += 1
        return packet

    def stop(self) -> None:
        self.stop_calls += 1

    def wait(self) -> None:
        self.wait_calls += 1


class FakeEngine:
    def __init__(
        self,
        state: dict[str, object],
        packets: list[dict[str, object]],
        *,
        next_error: Exception | None = None,
        analysis_error: Exception | None = None,
        engine_id: dict[str, str] | None = None,
        engine_options: dict[str, object] | None = None,
    ) -> None:
        self.state = state
        self.packets = packets
        self.next_error = next_error
        self.analysis_error = analysis_error
        self.id = engine_id or {"name": "Stockfish 16.1", "author": "Engine Team"}
        self.options = engine_options or {"EvalFile": "/opt/stockfish/net/stockfish.nnue"}
        self.configure_calls: list[dict[str, object]] = []
        self.analysis_calls: list[dict[str, object]] = []
        self.quit_calls = 0

    def configure(self, options: dict[str, object]) -> None:
        self.configure_calls.append(copy.deepcopy(options))

    def analysis(self, board, limit, *, multipv=None, info=None, **kwargs):
        self.analysis_calls.append(
            {
                "board": board.copy(stack=True),
                "limit": limit,
                "multipv": multipv,
                "info": info,
                "kwargs": copy.deepcopy(kwargs),
            }
        )
        if self.analysis_error is not None:
            raise self.analysis_error
        return FakeAnalysis(self.packets, next_error=self.next_error)

    def quit(self) -> None:
        self.quit_calls += 1


def _install_fake_engine(monkeypatch, *, packets, next_error=None, analysis_error=None, engine_options=None):
    state: dict[str, object] = {"popen_calls": 0, "paths": [], "timeouts": []}
    engine = FakeEngine(
        state,
        packets,
        next_error=next_error,
        analysis_error=analysis_error,
        engine_options=engine_options,
    )

    class FakeSimpleEngine:
        @staticmethod
        def popen_uci(path, timeout=None):
            state["popen_calls"] += 1
            state["paths"].append(path)
            state["timeouts"].append(timeout)
            state["engine"] = engine
            return engine

    monkeypatch.setattr(stockfish.chess.engine, "SimpleEngine", FakeSimpleEngine)
    return state, engine


def test_collect_reports_with_diagnostics_uses_latest_complete_checkpoint_and_counts_invalid_groups():
    root = chess.Board()
    collected = collect_reports_with_diagnostics(_load_transcript(), root, target_count=3)

    assert collected.snapshot is not None
    assert collected.snapshot.depth == 10
    assert [report.rank for report in collected.snapshot.reports] == [1, 2, 3]
    assert [report.pv[0] for report in collected.snapshot.reports] == ["e2e4", "d2d4", "g1f3"]
    assert [report.time_ms for report in collected.snapshot.reports] == [12, 13, 14]
    assert collected.diagnostics.bound_only_groups == 1
    assert collected.diagnostics.duplicate_root_groups == 1
    assert collected.diagnostics.inconsistent_groups == 0
    assert collected.diagnostics.incomplete_groups == 1
    validate_rank_order(collected.snapshot.reports, chess.WHITE)


def test_collector_accepts_captured_pinned_stockfish_incremental_transcript():
    captured = _load_captured_transcript()
    metadata = captured["metadata"]
    reports = captured["reports"]
    root = chess.Board(metadata["root_fen"])

    snapshot = collect_reports(reports, root, target_count=metadata["options"]["MultiPV"])

    # These are structural properties of the captured stream, rather than
    # assertions about what a future live engine search should rank or score.
    assert snapshot.depth == 4
    assert [report.rank for report in snapshot.reports] == [1, 2, 3]
    assert all(report.score.kind == "cp" for report in snapshot.reports)
    assert all(report.pv for report in snapshot.reports)
    assert all(chess.Move.from_uci(report.pv[0]) in root.legal_moves for report in snapshot.reports)
    validate_rank_order(snapshot.reports, root.turn)


def test_collect_reports_rejects_explicit_rank_other_than_one_for_single_candidate():
    root = chess.Board("7k/7R/7K/8/8/8/8/8 b - - 0 1")
    with pytest.raises(IncompleteCandidateSet) as excinfo:
        collect_reports(
            [
                {
                    "rank": 2,
                    "multipv": 1,
                    "depth": 10,
                    "score": {"kind": "cp", "value": 3},
                    "pv": ["h8g8"],
                }
            ],
            root,
            target_count=1,
        )
    assert excinfo.value.code == "INCOMPLETE_CANDIDATE_SET"


def test_evaluate_candidates_uses_one_process_one_analysis_and_network_hash_from_engine(monkeypatch):
    state, engine = _install_fake_engine(monkeypatch, packets=_load_transcript())
    result = stockfish.evaluate_candidates(
        chess.Board(),
        {
            "engine_path": "stockfish",
            "search_time": 0.5,
            "hard_timeout": 2.0,
            "threads": 1,
            "hash_mb": 64,
            "skill": 20,
            "candidate_target": 3,
        },
    )

    assert state["popen_calls"] == 1
    assert len(engine.analysis_calls) == 1
    assert engine.analysis_calls[0]["multipv"] == 3
    assert result.terminal is False
    assert result.snapshot is not None
    assert result.snapshot.depth == 10
    assert len(result.snapshot.reports) == 3
    assert result.engine_build == "Stockfish 16.1 (Engine Team)"
    assert result.network_hash == "stockfish.nnue"
    assert result.options["MultiPV"] == 3
    assert result.elapsed_search_ms >= 0
    assert result.diagnostics.bound_only_groups == 1
    assert result.diagnostics.duplicate_root_groups == 1
    assert result.diagnostics.incomplete_groups == 1
    assert [report.time_ms for report in result.snapshot.reports] == [12, 13, 14]


def test_network_hash_prefers_live_target_config_then_option_defaults():
    option = chess.engine.Option(
        name="EvalFile",
        type="string",
        default="/defaults/nnue/default.nnue",
        min=None,
        max=None,
        var=None,
    )
    live_engine = type(
        "LiveEngine",
        (),
        {
            "protocol": type("Protocol", (), {"target_config": {"EvalFile": "/configured/live.nnue"}})(),
            "options": {"EvalFile": option},
        },
    )()
    default_only_engine = type("DefaultOnlyEngine", (), {"options": {"EvalFile": option}})()

    assert stockfish._network_hash_from_engine(live_engine, None) == "live.nnue"
    assert stockfish._network_hash_from_engine(default_only_engine, None) == "default.nnue"


def test_evaluate_candidates_caps_multipv_to_legal_root_moves(monkeypatch):
    board = chess.Board("7k/7R/7K/8/8/8/8/8 b - - 0 1")
    packets = [
        {
            "depth": 8,
            "multipv": 1,
            "score": {"kind": "cp", "value": 27},
            "pv": ["h8g8", "h7h8"],
            "time": 0.01,
        }
    ]
    state, engine = _install_fake_engine(monkeypatch, packets=packets)

    result = stockfish.evaluate_candidates(
        board,
        {
            "engine_path": "stockfish",
            "search_time": 0.5,
            "hard_timeout": 2.0,
            "threads": 1,
            "hash_mb": 64,
            "skill": 20,
            "candidate_target": 3,
        },
    )

    assert engine.analysis_calls[0]["multipv"] == 1
    assert result.options["MultiPV"] == 1
    assert len(result.snapshot.reports) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"threads": 2, "hash_mb": 64},
        {"threads": 1, "hash_mb": 32},
    ],
)
def test_evaluate_candidates_rejects_bad_fixed_resource_settings_without_starting_engine(monkeypatch, overrides):
    state, engine = _install_fake_engine(
        monkeypatch,
        packets=_load_transcript(),
    )
    with pytest.raises(stockfish.InvalidEngineConfig):
        stockfish.evaluate_candidates(
            chess.Board(),
            {
                "engine_path": "stockfish",
                "search_time": 0.5,
                "hard_timeout": 2.0,
                "threads": overrides["threads"],
                "hash_mb": overrides["hash_mb"],
                "skill": 20,
                "candidate_target": 3,
            },
        )
    assert state["popen_calls"] == 0


def test_evaluate_candidates_rejects_invalid_candidate_target_without_starting_engine(monkeypatch):
    state, engine = _install_fake_engine(
        monkeypatch,
        packets=_load_transcript(),
    )
    with pytest.raises(stockfish.InvalidEngineConfig):
        stockfish.evaluate_candidates(
            chess.Board(),
            {
                "engine_path": "stockfish",
                "search_time": 0.5,
                "hard_timeout": 2.0,
                "threads": 1,
                "hash_mb": 64,
                "skill": 20,
                "candidate_target": 4,
            },
        )
    assert state["popen_calls"] == 0


def test_terminal_roots_return_a_terminal_result_without_starting_engine(monkeypatch):
    state, engine = _install_fake_engine(monkeypatch, packets=_load_transcript())
    result = stockfish.evaluate_candidates(
        chess.Board("7k/6R1/7K/8/8/8/8/8 b - - 0 1"),
        {
            "engine_path": "stockfish",
            "search_time": 0.5,
            "hard_timeout": 2.0,
            "threads": 1,
            "hash_mb": 64,
            "skill": 20,
            "candidate_target": 3,
        },
    )

    assert state["popen_calls"] == 0
    assert result.terminal is True
    assert result.snapshot is None
    assert result.engine_build == "terminal/no-search"
    assert result.options == {}


def test_timeout_and_crash_paths_do_not_succeed_with_a_checkpoint(monkeypatch):
    timeout_state, timeout_engine = _install_fake_engine(
        monkeypatch,
        packets=_load_transcript(),
        next_error=TimeoutError("uci deadline"),
    )
    with pytest.raises(stockfish.EngineTimeout):
        stockfish.evaluate_candidates(
            chess.Board(),
            {
                "engine_path": "stockfish",
                "search_time": 0.5,
                "hard_timeout": 2.0,
                "threads": 1,
                "hash_mb": 64,
                "skill": 20,
                "candidate_target": 3,
            },
        )
    assert timeout_state["popen_calls"] == 1
    assert len(timeout_engine.analysis_calls) == 1

    crash_state, crash_engine = _install_fake_engine(
        monkeypatch,
        packets=_load_transcript(),
        analysis_error=RuntimeError("boom"),
    )
    with pytest.raises(stockfish.EngineCrashed):
        stockfish.evaluate_candidates(
            chess.Board(),
            {
                "engine_path": "stockfish",
                "search_time": 0.5,
                "hard_timeout": 2.0,
                "threads": 1,
                "hash_mb": 64,
                "skill": 20,
                "candidate_target": 3,
            },
        )
    assert crash_state["popen_calls"] == 1
    assert len(crash_engine.analysis_calls) == 1


def test_watchdog_reaps_blocking_engine_and_joins_analysis_thread(monkeypatch):
    released = threading.Event()

    class Process:
        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True
            self.returncode = 0

        def kill(self):
            self.killed = True
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    process = Process()

    class BlockingAnalysis:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def next(self):
            # The watchdog must signal cleanup before this returns.
            released.wait(2.0)
            raise TimeoutError("blocked UCI stream released by watchdog")

    class BlockingEngine:
        def __init__(self):
            self.protocol = type("Protocol", (), {"transport": type("Transport", (), {"_proc": process})()})()
            self.options = {}
            self.id = {"name": "blocking", "author": "fixture"}
            self.quit_calls = 0

        def configure(self, options):
            pass

        def analysis(self, board, limit, *, multipv=None, info=None, **kwargs):
            return BlockingAnalysis()

        def quit(self):
            self.quit_calls += 1
            released.set()

    engine = BlockingEngine()

    class FakeSimpleEngine:
        @staticmethod
        def popen_uci(path, timeout=None):
            return engine

    monkeypatch.setattr(stockfish.chess.engine, "SimpleEngine", FakeSimpleEngine)
    with pytest.raises(stockfish.EngineTimeout):
        stockfish.evaluate_candidates(
            chess.Board(),
            {
                "engine_path": "stockfish",
                "search_time": 0.05,
                "hard_timeout": 0.6,
                "threads": 1,
                "hash_mb": 64,
                "skill": 20,
                "candidate_target": 3,
            },
        )

    assert released.is_set()
    assert process.returncode == 0
    assert process.terminated or process.killed
    assert not any(thread.name == "stockfish-candidates" for thread in threading.enumerate())


def test_pathological_engine_timeout_is_bounded_and_blocks_overlapping_retry(monkeypatch):
    release = threading.Event()
    state = {"popen_calls": 0}

    class Process:
        returncode = None

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    process = Process()

    class Analysis:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def next(self):
            while not release.is_set():
                time.sleep(0.01)
            raise TimeoutError("released")

    class Engine:
        def __init__(self):
            transport = type("Transport", (), {"_proc": process})()
            self.protocol = type("Protocol", (), {"transport": transport})()

        def configure(self, options):
            pass

        def analysis(self, board, limit, *, multipv=None, info=None, **kwargs):
            return Analysis()

        def quit(self):
            # Cleanup itself is also hostile until the test releases it.
            release.wait(2.0)

    engine = Engine()

    class SimpleEngine:
        @staticmethod
        def popen_uci(path, timeout=None):
            state["popen_calls"] += 1
            return engine

    monkeypatch.setattr(stockfish.chess.engine, "SimpleEngine", SimpleEngine)
    started = time.monotonic()
    with pytest.raises(stockfish.EngineTimeout):
        stockfish.evaluate_candidates(
            chess.Board(),
            {"engine_path": "stockfish", "search_time": 0.05, "hard_timeout": 0.6, "threads": 1, "hash_mb": 64, "candidate_target": 3},
        )
    assert time.monotonic() - started < 1.0
    with pytest.raises(stockfish.EngineTimeout, match="previous engine attempt"):
        stockfish.evaluate_candidates(
            chess.Board(),
            {"engine_path": "stockfish", "search_time": 0.05, "hard_timeout": 0.6, "threads": 1, "hash_mb": 64, "candidate_target": 3},
        )
    assert state["popen_calls"] == 1
    release.set()
    deadline = time.monotonic() + 1.0
    while any(thread.name == "stockfish-candidates" for thread in threading.enumerate()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(thread.name == "stockfish-candidates" for thread in threading.enumerate())


def test_process_wait_without_timeout_api_is_bounded():
    release = threading.Event()

    class Process:
        returncode = None

        def wait(self):
            release.wait(2.0)

    process = Process()
    started = time.monotonic()
    assert stockfish._wait_process(process, 0.02, object()) is False
    assert time.monotonic() - started < 0.5
    release.set()


def test_real_engine_candidate_snapshot_is_legal_and_bounded():
    engine_path = shutil.which("stockfish")
    if engine_path is None:
        pytest.skip("stockfish binary is unavailable")

    result = stockfish.evaluate_candidates(
        chess.Board(),
        {
            "engine_path": engine_path,
            "search_time": 0.2,
            "hard_timeout": 2.0,
            "threads": 1,
            "hash_mb": 64,
            "skill": 20,
            "candidate_target": 3,
        },
    )

    assert result.terminal is False
    assert result.snapshot is not None
    assert len(result.snapshot.reports) == 3
    assert result.snapshot.depth >= 1
    assert result.options["MultiPV"] == 3
    assert result.elapsed_search_ms >= 0
    assert all(report.depth >= 1 for report in result.snapshot.reports)
    assert all(report.pv for report in result.snapshot.reports)
    assert len({report.pv[0] for report in result.snapshot.reports}) == len(result.snapshot.reports)
    assert all(chess.Move.from_uci(report.pv[0]) in chess.Board().legal_moves for report in result.snapshot.reports)
    validate_rank_order(result.snapshot.reports, chess.WHITE)


def _pinned_stockfish_path() -> str | None:
    return os.environ.get("STOCKFISH_PATH") or shutil.which("stockfish")


def _real_candidate_config(path: str, *, target: int = 3, search_time: float = 0.2) -> dict[str, object]:
    return {
        "engine_path": path,
        "search_time": search_time,
        "hard_timeout": 2.0,
        "threads": 1,
        "hash_mb": 64,
        "skill": 20,
        "candidate_target": target,
    }


def _assert_real_candidate_invariants(fen: str) -> stockfish.CandidateSearchResult:
    path = _pinned_stockfish_path()
    if path is None:
        pytest.skip("pinned Stockfish binary is unavailable")

    board = chess.Board(fen)
    result = stockfish.evaluate_candidates(board, _real_candidate_config(path))
    if board.is_game_over():
        assert result.terminal is True
        assert result.snapshot is None
        assert result.options == {}
        return result

    assert result.terminal is False
    assert result.snapshot is not None
    expected_count = min(3, board.legal_moves.count())
    assert len(result.snapshot.reports) == expected_count
    assert {report.rank for report in result.snapshot.reports} == set(range(1, expected_count + 1))
    assert result.options["MultiPV"] == expected_count
    assert result.options["Threads"] == 1
    assert result.options["Hash"] == 64

    root_moves = set()
    for report in result.snapshot.reports:
        root_moves.add(report.pv[0])
        assert chess.Move.from_uci(report.pv[0]) in board.legal_moves
        replay_candidate(board, report, max_plies=6)
    assert len(root_moves) == expected_count
    normalize_and_compare(result.snapshot, board.turn)
    validate_rank_order(result.snapshot.reports, board.turn)
    return result


@pytest.mark.parametrize(
    "fen",
    [
        # Ordinary root with Black to move exercises White-oriented score
        # normalization and root-player ranking.
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1",
        # A nonterminal root with exactly one legal move must request K=1.
        "7k/7R/7K/8/8/8/8/8 b - - 0 1",
    ],
    ids=["black-to-move", "one-legal-root"],
)
def test_pinned_stockfish_ordinary_roots_return_exact_legal_candidate_count(fen: str):
    _assert_real_candidate_invariants(fen)


def test_pinned_stockfish_finds_a_mate_valued_nonterminal_root():
    # White has a forced mate in one, but the supplied board is not terminal.
    result = _assert_real_candidate_invariants("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    assert result.snapshot is not None
    assert any(report.score.kind == "mate" for report in result.snapshot.reports)


@pytest.mark.parametrize(
    "fen",
    [
        "7k/6R1/7K/8/8/8/8/8 b - - 0 1",  # checkmate
        "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",  # stalemate
    ],
    ids=["checkmate", "stalemate"],
)
def test_pinned_stockfish_terminal_roots_use_no_engine_and_no_candidates(fen: str):
    result = _assert_real_candidate_invariants(fen)
    assert result.terminal is True
    assert result.snapshot is None


def test_unreapable_engine_process_cannot_publish_or_start_a_retry(monkeypatch):
    class UnreapableProcess:
        returncode = None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return None

    process = UnreapableProcess()
    engine = FakeEngine({}, _load_transcript())
    engine.protocol = type(
        "Protocol", (), {"transport": type("Transport", (), {"_proc": process})()}
    )()
    state = {"popen_calls": 0}

    class SimpleEngine:
        @staticmethod
        def popen_uci(path, timeout=None):
            state["popen_calls"] += 1
            return engine

    monkeypatch.setattr(stockfish.chess.engine, "SimpleEngine", SimpleEngine)
    config = {"engine_path": "stockfish", "search_time": 0.05, "hard_timeout": 0.6, "threads": 1, "hash_mb": 64, "candidate_target": 3}
    with pytest.raises(stockfish.EngineTimeout):
        stockfish.evaluate_candidates(chess.Board(), config)
    assert state["popen_calls"] == 1
    with pytest.raises(stockfish.EngineTimeout, match="previous engine attempt"):
        stockfish.evaluate_candidates(chess.Board(), config)
    assert state["popen_calls"] == 1
