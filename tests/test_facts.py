from __future__ import annotations

import chess

from app.explanations.render import render_factual_explanation
from app.facts.extract import build_factual_result, extract_facts
from app.facts.models import TerminalState

EXAMPLE_FEN = "6k1/5ppp/8/8/3P4/8/5PPP/6K1 w - - 0 1"
TRIPLE_PAWN_FEN = "k7/8/8/8/2P5/2P5/2P5/7K w - - 0 1"
PAWNLESS_FEN = "k7/8/8/8/8/8/8/7K w - - 0 1"


def test_worked_example_matches_plan_fixture():
    board = chess.Board(EXAMPLE_FEN)
    facts = extract_facts(board)
    assert facts.model_dump(mode="json") == {
        "material": {
            "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 4},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 3},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
        },
        "pawns": {
            "white": {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
            "black": {"isolated": [], "doubled_files": {}, "passed": []},
        },
        "files": {
            "open": ["a", "b", "c", "e"],
            "semi_open": {"white": [], "black": ["d"]},
        },
    }
    result = build_factual_result(board)
    assert result.model_dump(mode="json") == {
        "version": 1,
        "facts": facts.model_dump(mode="json"),
        "explanation": "White has one more pawn than Black. White's d4-pawn is isolated and passed. The a-, b-, c-, and e-files are open; the d-file is semi-open for Black.",
    }


def test_renderer_handles_same_file_groups_and_terminal_prose():
    board = chess.Board(TRIPLE_PAWN_FEN)
    facts = extract_facts(board)
    text = render_factual_explanation(facts, TerminalState(kind="none"))
    assert "White's c2-, c3-, and c4-pawns are isolated and passed." in text
    assert "White has three pawns on the c-file." in text

    terminal_text = render_factual_explanation(
        extract_facts(chess.Board(PAWNLESS_FEN)),
        TerminalState(kind="stalemate"),
    )
    assert terminal_text == "The position is stalemate."

    mate_text = render_factual_explanation(
        extract_facts(chess.Board(PAWNLESS_FEN)),
        TerminalState(kind="checkmate", winner="white"),
    )
    assert mate_text == "Black is checkmated."
