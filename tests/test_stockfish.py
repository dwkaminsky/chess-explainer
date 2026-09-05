"""Focused adapter tests; the process boundary is represented by test doubles."""

from types import SimpleNamespace

import pytest

from app import stockfish


class FakeScore:
    def __init__(self, cp=None, mate=None, *, bound=False):
        self.cp = cp
        self.mate_value = mate
        self.bound = bound

    def pov(self, _colour):
        return self

    def mate(self):
        return self.mate_value

    def score(self, mate_score=None):
        return self.cp

    def is_lowerbound(self):
        return self.bound

    def is_upperbound(self):
        return False


def test_normalize_score_is_white_perspective(monkeypatch):
    monkeypatch.setattr(stockfish, "chess", SimpleNamespace(WHITE=True))
    assert stockfish.normalize_score({"score": FakeScore(cp=34)}) == 34
    assert stockfish.normalize_score({"score": FakeScore(cp=-34)}) == -34


def test_real_pov_scores_normalize_to_the_same_white_score():
    chess = pytest.importorskip("chess")
    white_relative = chess.engine.PovScore(chess.engine.Cp(34), chess.WHITE)
    black_relative = chess.engine.PovScore(chess.engine.Cp(-34), chess.BLACK)
    assert stockfish.normalize_score({"score": white_relative}) == 34
    assert stockfish.normalize_score({"score": black_relative}) == 34


def test_normalize_mate_keeps_mate_separate(monkeypatch):
    monkeypatch.setattr(stockfish, "chess", SimpleNamespace(WHITE=True))
    assert stockfish.normalize_score({"score": FakeScore(mate=3)}) == stockfish.MateResult("white", 3)
    assert stockfish.normalize_score({"score": FakeScore(mate=-2)}) == stockfish.MateResult("black", 2)


def test_bound_or_missing_scores_are_not_false_zero(monkeypatch):
    monkeypatch.setattr(stockfish, "chess", SimpleNamespace(WHITE=True))
    with pytest.raises(stockfish.MissingScore):
        stockfish.normalize_score({"score": FakeScore(cp=0, bound=True)})
    with pytest.raises(stockfish.MissingScore):
        stockfish.normalize_score({})


def test_synchronous_engine_timeout_is_retryable(monkeypatch):
    class Board:
        turn = True

        def __init__(self, fen): pass

        def is_checkmate(self): return False
        def is_stalemate(self): return False
        def is_insufficient_material(self): return False
        def is_seventyfive_moves(self): return False
        def is_variant_draw(self): return False
        def is_variant_loss(self): return False
        def is_variant_win(self): return False

    class Engine:
        def configure(self, options): pass
        def analyse(self, board, limit, multipv=1): raise TimeoutError("uci deadline")
        def quit(self): pass

    class SimpleEngine:
        @staticmethod
        def popen_uci(path, timeout=None): return Engine()

    def limit(**kwargs): return kwargs

    engine_module = SimpleNamespace(SimpleEngine=SimpleEngine, Limit=limit)

    monkeypatch.setattr(stockfish, "chess", SimpleNamespace(Board=Board, WHITE=True, BLACK=False, engine=engine_module))
    with pytest.raises(stockfish.EngineTimeout):
        stockfish.evaluate_fen("nonterminal fen", {"engine_path": "unused"})


def test_terminal_board_is_handled_without_starting_engine(monkeypatch):
    class TerminalBoard:
        turn = True

        def is_checkmate(self):
            return True

        def is_stalemate(self):
            return False

        def is_insufficient_material(self):
            return False

    fake_chess = SimpleNamespace(Board=lambda _fen: TerminalBoard(), WHITE=True, BLACK=False)
    monkeypatch.setattr(stockfish, "chess", fake_chess)
    monkeypatch.setattr(stockfish, "_evaluate_engine", lambda *args: pytest.fail("engine should not start"))
    assert stockfish.evaluate_fen("terminal fen", {"engine_path": "unused"}) == stockfish.MateResult("black", 0)


def test_terminal_draw_and_checkmate_are_separate_results():
    chess = pytest.importorskip("chess")
    draw = chess.Board("7k/5Q2/7K/8/8/8/8/8 b - - 0 1")
    mate = chess.Board("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")
    assert stockfish._terminal_result(draw) == 0
    assert stockfish._terminal_result(mate) == stockfish.MateResult("white", 0)
