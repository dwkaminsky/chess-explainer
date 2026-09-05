from __future__ import annotations

import chess
import pytest

from app.facts.extract import extract_facts
from app.fen import normalize_fen


CASES = [
    pytest.param(
        "4k3/8/8/8/3P4/8/3P4/4K3 w - - 0 1",
        {"isolated": ["d2", "d4"], "doubled_files": {"d": ["d2", "d4"]}, "passed": ["d2", "d4"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="double-2",
    ),
    pytest.param(
        "4k3/8/2P5/8/2P5/8/2P5/4K3 w - - 0 1",
        {"isolated": ["c2", "c4", "c6"], "doubled_files": {"c": ["c2", "c4", "c6"]}, "passed": ["c2", "c4", "c6"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="double-3",
    ),
    pytest.param(
        "4k3/8/8/P7/P7/P7/P7/4K3 w - - 0 1",
        {"isolated": ["a2", "a3", "a4", "a5"], "doubled_files": {"a": ["a2", "a3", "a4", "a5"]}, "passed": ["a2", "a3", "a4", "a5"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="double-4",
    ),
    pytest.param(
        "4k3/8/8/3p4/3P4/8/8/4K3 w - - 0 1",
        {"isolated": ["d4"], "doubled_files": {}, "passed": []},
        {"isolated": ["d5"], "doubled_files": {}, "passed": []},
        id="same-file-opposite-colors",
    ),
    pytest.param(
        "4k3/8/8/2p5/3P4/8/8/4K3 w - - 0 1",
        {"isolated": ["d4"], "doubled_files": {}, "passed": []},
        {"isolated": ["c5"], "doubled_files": {}, "passed": []},
        id="enemy-ahead-adjacent-file",
    ),
    pytest.param(
        "4k3/8/8/8/3Pp3/8/8/4K3 w - - 0 1",
        {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
        {"isolated": ["e4"], "doubled_files": {}, "passed": ["e4"]},
        id="enemy-same-rank",
    ),
    pytest.param(
        "4k3/8/8/3B4/3P4/8/8/4K3 w - - 0 1",
        {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="friendly-blocker",
    ),
    pytest.param(
        "4k3/8/8/3r4/3P4/8/8/4K3 w - - 0 1",
        {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="non-pawn-blocker",
    ),
    pytest.param(
        "4k3/8/8/8/8/8/P7/4K3 w - - 0 1",
        {"isolated": ["a2"], "doubled_files": {}, "passed": ["a2"]},
        {"isolated": [], "doubled_files": {}, "passed": []},
        id="white-a-edge",
    ),
    pytest.param(
        "4k3/7p/8/8/8/8/8/4K3 b - - 0 1",
        {"isolated": [], "doubled_files": {}, "passed": []},
        {"isolated": ["h7"], "doubled_files": {}, "passed": ["h7"]},
        id="black-h-edge",
    ),
    pytest.param(
        "4k3/8/8/3Pp3/8/8/8/4K3 w - e6 0 1",
        {"isolated": ["d5"], "doubled_files": {}, "passed": ["d5"]},
        {"isolated": ["e5"], "doubled_files": {}, "passed": ["e5"]},
        id="en-passant-available",
    ),
    pytest.param(
        "4k3/8/8/8/3P4/3p4/8/4K3 w - - 0 1",
        {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
        {"isolated": ["d3"], "doubled_files": {}, "passed": ["d3"]},
        id="enemy-behind",
    ),
]


@pytest.mark.parametrize("fen, expected_white, expected_black", CASES)
def test_factual_pawn_direction_matrix(
    fen: str,
    expected_white: dict[str, object],
    expected_black: dict[str, object],
) -> None:
    assert normalize_fen(fen) == fen

    board = chess.Board(fen)
    before = board.fen()

    facts = extract_facts(board)

    assert board.fen() == before
    assert facts.pawns.white.model_dump(mode="json") == expected_white
    assert facts.pawns.black.model_dump(mode="json") == expected_black
