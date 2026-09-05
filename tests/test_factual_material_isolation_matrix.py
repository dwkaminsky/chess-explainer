from __future__ import annotations

import chess
import pytest

from app.facts.extract import extract_facts
from app.fen import normalize_fen


CASES = [
    pytest.param(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        {
            "white": {"queen": 1, "rook": 2, "bishop": 2, "knight": 2, "pawn": 8},
            "black": {"queen": 1, "rook": 2, "bishop": 2, "knight": 2, "pawn": 8},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
        },
        {"isolated": [], "doubled_files": {}, "passed": []},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="initial-equal",
    ),
    pytest.param(
        "6k1/5ppp/8/8/3P4/8/5PPP/6K1 w - - 0 1",
        {
            "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 4},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 3},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
        },
        {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="extra-white-pawn",
    ),
    pytest.param(
        "6k1/5ppp/8/3p4/8/8/5PPP/6K1 b - - 0 1",
        {
            "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 3},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 4},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": -1},
        },
        {"isolated": [], "doubled_files": {}, "passed": []},
        {"isolated": ["d5"], "doubled_files": {}, "passed": ["d5"]},
        id="extra-black-pawn",
    ),
    pytest.param(
        "4k3/8/8/8/8/8/8/R3K2b w - - 0 1",
        {
            "white": {"queen": 0, "rook": 1, "bishop": 0, "knight": 0, "pawn": 0},
            "black": {"queen": 0, "rook": 0, "bishop": 1, "knight": 0, "pawn": 0},
            "white_minus_black": {"queen": 0, "rook": 1, "bishop": -1, "knight": 0, "pawn": 0},
        },
        {"isolated": [], "doubled_files": {}, "passed": []},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="rook-bishop-imbalance",
    ),
    pytest.param(
        "4k3/8/8/8/8/8/8/Q3K2Q w - - 0 1",
        {
            "white": {"queen": 2, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
            "white_minus_black": {"queen": 2, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
        },
        {"isolated": [], "doubled_files": {}, "passed": []},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="promoted-queens",
    ),
    pytest.param(
        "4k3/8/8/8/3P4/8/8/4K3 w - - 0 1",
        {
            "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
        },
        {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="central-isolated",
    ),
    pytest.param(
        "4k3/8/8/8/8/8/P7/4K3 w - - 0 1",
        {
            "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
        },
        {"isolated": ["a2"], "doubled_files": {}, "passed": ["a2"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="a-edge-isolated",
    ),
    pytest.param(
        "4k3/7p/8/8/8/8/8/4K3 b - - 0 1",
        {
            "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": -1},
        },
        {"isolated": [], "doubled_files": {}, "passed": []},
        {"isolated": ["h7"], "doubled_files": {}, "passed": ["h7"]},
        id="h-edge-isolated",
    ),
    pytest.param(
        "4k3/1P6/8/8/8/8/P7/4K3 w - - 0 1",
        {
            "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 2},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 2},
        },
        {"isolated": [], "doubled_files": {}, "passed": ["a2", "b7"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="far-apart-adjacent-files",
    ),
    pytest.param(
        "4k3/8/2P5/8/2P5/8/2P5/4K3 w - - 0 1",
        {
            "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 3},
            "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
            "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 3},
        },
        {"isolated": ["c2", "c4", "c6"], "doubled_files": {"c": ["c2", "c4", "c6"]}, "passed": ["c2", "c4", "c6"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="doubled-and-isolated",
    ),
]


@pytest.mark.parametrize("fen, material, white_pawns, black_pawns", CASES)
def test_extract_facts_matrix_preserves_the_board_and_reports_literal_fields(
    fen: str,
    material: dict[str, dict[str, int]],
    white_pawns: dict[str, object],
    black_pawns: dict[str, object],
) -> None:
    assert normalize_fen(fen) == fen

    board = chess.Board(fen)
    before = board.fen()

    facts = extract_facts(board)

    assert board.fen() == before
    assert facts.material.model_dump(mode="json") == material
    assert facts.pawns.white.model_dump(mode="json") == white_pawns
    assert facts.pawns.black.model_dump(mode="json") == black_pawns
