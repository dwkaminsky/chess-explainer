from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import chess
import pytest

from app.explanations.render import render_factual_explanation
from app.facts.extract import extract_facts, terminal_context_from_board
from app.facts.models import PositionFacts, TerminalState
from app.fen import normalize_fen

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PAWNLESS_ROOK_FEN = "4k2r/8/8/8/8/8/8/R3K3 w - - 0 1"
FILE_MATRIX_FEN = "4k3/1p6/8/2p5/8/8/P1P2R2/4K3 w - - 0 1"
SAMPLE_FEN = "6k1/5ppp/8/8/3P4/8/5PPP/6K1 w - - 0 1"
DOUBLE_GROUP_FEN = "4k3/8/2P5/8/2P1P3/8/2P1P3/4K3 w - - 0 1"
BLOCKED_PASSED_FEN = "4k3/8/3p4/4p3/3P4/8/8/4K3 w - - 0 1"
BLACK_PASSED_FEN = "4k3/8/8/3p4/8/8/8/4K3 b - - 0 1"
EN_PASSANT_FEN = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
CHECKMATE_FEN = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
STALEMATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"


@dataclass(frozen=True)
class MatrixCase:
    name: str
    fen: str
    expected_facts: dict[str, Any]
    expected_explanation: str | None = None
    terminal_state: TerminalState | None = None


CASES = [
    MatrixCase(
        name="initial_position",
        fen=START_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 1, "rook": 2, "bishop": 2, "knight": 2, "pawn": 8},
                "black": {"queen": 1, "rook": 2, "bishop": 2, "knight": 2, "pawn": 8},
                "white_minus_black": {
                    "queen": 0,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": 0,
                },
            },
            "pawns": {
                "white": {"isolated": [], "doubled_files": {}, "passed": []},
                "black": {"isolated": [], "doubled_files": {}, "passed": []},
            },
            "files": {"open": [], "semi_open": {"white": [], "black": []}},
        },
        expected_explanation=(
            "Both sides have the same material. Neither side has isolated, doubled, or passed pawns."
        ),
    ),
    MatrixCase(
        name="pawnless_rook_balance",
        fen=PAWNLESS_ROOK_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 0, "rook": 1, "bishop": 0, "knight": 0, "pawn": 0},
                "black": {"queen": 0, "rook": 1, "bishop": 0, "knight": 0, "pawn": 0},
                "white_minus_black": {
                    "queen": 0,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": 0,
                },
            },
            "pawns": {
                "white": {"isolated": [], "doubled_files": {}, "passed": []},
                "black": {"isolated": [], "doubled_files": {}, "passed": []},
            },
            "files": {
                "open": ["a", "b", "c", "d", "e", "f", "g", "h"],
                "semi_open": {"white": [], "black": []},
            },
        },
        expected_explanation=(
            "Both sides have the same material. Neither side has pawns. Every file is open."
        ),
    ),
    MatrixCase(
        name="file_occupancy_matrix",
        fen=FILE_MATRIX_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 0, "rook": 1, "bishop": 0, "knight": 0, "pawn": 2},
                "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 2},
                "white_minus_black": {
                    "queen": 0,
                    "rook": 1,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": 0,
                },
            },
            "pawns": {
                "white": {"isolated": ["a2", "c2"], "doubled_files": {}, "passed": []},
                "black": {"isolated": [], "doubled_files": {}, "passed": []},
            },
            "files": {
                "open": ["d", "e", "f", "g", "h"],
                "semi_open": {"white": ["b"], "black": ["a"]},
            },
        },
    ),
    MatrixCase(
        name="sample_isolated_and_passed",
        fen=SAMPLE_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 4},
                "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 3},
                "white_minus_black": {
                    "queen": 0,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": 1,
                },
            },
            "pawns": {
                "white": {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
                "black": {"isolated": [], "doubled_files": {}, "passed": []},
            },
            "files": {
                "open": ["a", "b", "c", "e"],
                "semi_open": {"white": [], "black": ["d"]},
            },
        },
        expected_explanation=(
            "White has one more pawn than Black. White's d4-pawn is isolated and passed. "
            "The a-, b-, c-, and e-files are open; the d-file is semi-open for Black."
        ),
    ),
    MatrixCase(
        name="mixed_doubled_groups",
        fen=DOUBLE_GROUP_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 5},
                "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
                "white_minus_black": {
                    "queen": 0,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": 5,
                },
            },
            "pawns": {
                "white": {
                    "isolated": ["c2", "c4", "c6", "e2", "e4"],
                    "doubled_files": {"c": ["c2", "c4", "c6"], "e": ["e2", "e4"]},
                    "passed": ["c2", "c4", "c6", "e2", "e4"],
                },
                "black": {"isolated": [], "doubled_files": {}, "passed": []},
            },
            "files": {
                "open": ["a", "b", "d", "f", "g", "h"],
                "semi_open": {"white": [], "black": ["c", "e"]},
            },
        },
        expected_explanation=(
            "White has five more pawns than Black. White has three pawns on the c-file: c2, c4, "
            "and c6; those pawns are isolated and passed; doubled pawns on e2 and e4; those pawns "
            "are isolated and passed. The a-, b-, d-, f-, g-, and h-files are open; the c- and "
            "e-files are semi-open for Black."
        ),
    ),
    MatrixCase(
        name="passed_blocked_ahead_same_and_adjacent",
        fen=BLOCKED_PASSED_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
                "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 2},
                "white_minus_black": {
                    "queen": 0,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": -1,
                },
            },
            "pawns": {
                "white": {"isolated": ["d4"], "doubled_files": {}, "passed": []},
                "black": {"isolated": [], "doubled_files": {}, "passed": []},
            },
            "files": {
                "open": ["a", "b", "c", "f", "g", "h"],
                "semi_open": {"white": ["e"], "black": []},
            },
        },
    ),
    MatrixCase(
        name="black_passed_simple",
        fen=BLACK_PASSED_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
                "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
                "white_minus_black": {
                    "queen": 0,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": -1,
                },
            },
            "pawns": {
                "white": {"isolated": [], "doubled_files": {}, "passed": []},
                "black": {"isolated": ["d5"], "doubled_files": {}, "passed": ["d5"]},
            },
            "files": {
                "open": ["a", "b", "c", "e", "f", "g", "h"],
                "semi_open": {"white": ["d"], "black": []},
            },
        },
        expected_explanation=(
            "Black has one more pawn than White. Black's d5-pawn is isolated and passed. "
            "The a-, b-, c-, e-, f-, g-, and h-files are open; the d-file is semi-open for White."
        ),
    ),
    MatrixCase(
        name="en_passant_limited",
        fen=EN_PASSANT_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
                "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
                "white_minus_black": {
                    "queen": 0,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": 0,
                },
            },
            "pawns": {
                "white": {"isolated": ["e5"], "doubled_files": {}, "passed": ["e5"]},
                "black": {"isolated": ["d5"], "doubled_files": {}, "passed": ["d5"]},
            },
            "files": {
                "open": ["a", "b", "c", "f", "g", "h"],
                "semi_open": {"white": ["d"], "black": ["e"]},
            },
        },
        expected_explanation=(
            "Both sides have the same material. White's e5-pawn is isolated and passed. "
            "Black's d5-pawn is isolated and passed. The a-, b-, c-, f-, g-, and h-files are "
            "open; the d-file is semi-open for White; the e-file is semi-open for Black."
        ),
    ),
    MatrixCase(
        name="checkmate_terminal",
        fen=CHECKMATE_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 1, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
                "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
                "white_minus_black": {
                    "queen": 1,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": 0,
                },
            },
            "pawns": {
                "white": {"isolated": [], "doubled_files": {}, "passed": []},
                "black": {"isolated": [], "doubled_files": {}, "passed": []},
            },
            "files": {
                "open": ["a", "b", "c", "d", "e", "f", "g", "h"],
                "semi_open": {"white": [], "black": []},
            },
        },
        expected_explanation="Black is checkmated. White has one more queen than Black.",
        terminal_state=TerminalState(kind="checkmate", winner="white"),
    ),
    MatrixCase(
        name="stalemate_terminal",
        fen=STALEMATE_FEN,
        expected_facts={
            "material": {
                "white": {"queen": 1, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
                "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 0},
                "white_minus_black": {
                    "queen": 1,
                    "rook": 0,
                    "bishop": 0,
                    "knight": 0,
                    "pawn": 0,
                },
            },
            "pawns": {
                "white": {"isolated": [], "doubled_files": {}, "passed": []},
                "black": {"isolated": [], "doubled_files": {}, "passed": []},
            },
            "files": {
                "open": ["a", "b", "c", "d", "e", "f", "g", "h"],
                "semi_open": {"white": [], "black": []},
            },
        },
        expected_explanation="The game is stalemated. White has one more queen than Black.",
        terminal_state=TerminalState(kind="stalemate"),
    ),
]

assert len(CASES) == 10

UNSUPPORTED_TOKENS = ("attack", "advantage", "better", "recommend", "should", "weak")


def _assert_board_immutable(board: chess.Board, before: str) -> None:
    assert board.fen() == before


def _assert_no_unsupported_language(text: str) -> None:
    lowered = text.lower()
    for token in UNSUPPORTED_TOKENS:
        assert token not in lowered


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_fen_fixtures_are_normalized_and_valid(case: MatrixCase) -> None:
    assert normalize_fen(case.fen) == case.fen


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_extracted_facts_match_the_literal_matrix(case: MatrixCase) -> None:
    board = chess.Board(case.fen)
    before = board.fen()

    facts = extract_facts(board)

    _assert_board_immutable(board, before)
    assert facts.model_dump(mode="json") == case.expected_facts


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.expected_explanation is not None],
    ids=[case.name for case in CASES if case.expected_explanation is not None],
)
def test_rendered_explanations_are_deterministic_and_bounded(case: MatrixCase) -> None:
    board = chess.Board(case.fen)
    before = board.fen()
    terminal_state = terminal_context_from_board(board)
    if case.terminal_state is not None:
        assert terminal_state.kind == case.terminal_state.kind
        assert terminal_state.winner == case.terminal_state.winner
    _assert_board_immutable(board, before)

    facts = PositionFacts.model_validate(case.expected_facts)
    first = render_factual_explanation(
        facts,
        terminal_state if case.terminal_state is not None else None,
    )
    second = render_factual_explanation(
        facts,
        terminal_state if case.terminal_state is not None else None,
    )

    assert first == second == case.expected_explanation
    assert len(first.split()) <= 120
    assert 2 <= first.count(".") <= 4
    _assert_no_unsupported_language(first)
