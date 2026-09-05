from __future__ import annotations

import chess

from app.facts.extract import build_factual_result, extract_facts, terminal_context_from_board

PLAN_FEN = "6k1/5ppp/8/8/3P4/8/5PPP/6K1 w - - 0 1"
PROMOTED_QUEENS_FEN = "4k3/8/8/8/8/8/8/Q3K2Q w - - 0 1"
FAR_APART_EDGE_FEN = "4k3/1P6/8/8/8/8/P7/4K3 w - - 0 1"
TRIPLE_FILE_FEN = "4k3/8/2P5/8/2P5/8/2P5/4K3 w - - 0 1"
CHECKMATE_FEN = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"

EXPECTED_PLAN_FACTS = {
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


def test_extract_facts_matches_the_planned_fixture_and_keeps_board_state():
    board = chess.Board(PLAN_FEN)
    before = board.fen()

    facts = extract_facts(board)

    assert board.fen() == before
    assert facts.model_dump(mode="json") == EXPECTED_PLAN_FACTS


def test_extract_material_counts_promoted_queens():
    board = chess.Board(PROMOTED_QUEENS_FEN)

    facts = extract_facts(board)

    assert facts.material.white.queen == 2
    assert facts.material.black.queen == 0
    assert facts.material.white_minus_black.queen == 2


def test_extract_pawn_facts_treats_far_apart_adjacent_files_as_not_isolated():
    board = chess.Board(FAR_APART_EDGE_FEN)

    facts = extract_facts(board)

    assert facts.pawns.white.isolated == []
    assert facts.pawns.white.doubled_files == {}
    assert facts.pawns.white.passed == ["a2", "b7"]


def test_extract_pawn_facts_sorts_three_pawns_on_one_file():
    board = chess.Board(TRIPLE_FILE_FEN)

    facts = extract_facts(board)

    assert facts.pawns.white.isolated == ["c2", "c4", "c6"]
    assert facts.pawns.white.doubled_files == {"c": ["c2", "c4", "c6"]}
    assert facts.pawns.white.passed == ["c2", "c4", "c6"]


def test_terminal_context_detects_checkmate_and_result_is_stable():
    board = chess.Board(CHECKMATE_FEN)
    before = board.fen()

    first = build_factual_result(board)
    second = build_factual_result(board)
    state = terminal_context_from_board(board)

    assert board.fen() == before
    assert state.kind == "checkmate"
    assert state.winner == "white"
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.version == 1
