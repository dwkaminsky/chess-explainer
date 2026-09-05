from __future__ import annotations

import chess
import chess.engine
import pytest

from app.candidates.models import CandidateReport, CandidateSnapshot, CpScore, MateScore
from app.candidates.scores import (
    InvalidCandidateOrdering,
    InvalidCandidateScore,
    compare_scores,
    cp_to_pawn_units,
    deserialize_pawn_units,
    gap_to_best,
    normalize_and_compare,
    normalize_pov_score,
    pawn_units_to_cp,
    serialize_pawn_units,
    validate_rank_order,
)


def test_normalize_pov_score_handles_white_black_and_mates() -> None:
    assert normalize_pov_score(chess.engine.PovScore(chess.engine.Cp(34), chess.WHITE)) == CpScore(
        kind="cp",
        value=34,
    )
    assert normalize_pov_score(chess.engine.PovScore(chess.engine.Cp(34), chess.BLACK)) == CpScore(
        kind="cp",
        value=-34,
    )
    assert normalize_pov_score(chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE)) == MateScore(
        kind="mate",
        winner="white",
        moves=3,
    )
    assert normalize_pov_score(chess.engine.PovScore(chess.engine.Mate(2), chess.BLACK)) == MateScore(
        kind="mate",
        winner="black",
        moves=2,
    )


def test_rank_order_and_gaps_follow_root_side() -> None:
    white_scores = [CpScore(kind="cp", value=34), CpScore(kind="cp", value=10), CpScore(kind="cp", value=10)]
    black_scores = [CpScore(kind="cp", value=-40), CpScore(kind="cp", value=10), CpScore(kind="cp", value=34)]

    validate_rank_order(white_scores, chess.WHITE)
    validate_rank_order(black_scores, chess.BLACK)

    assert compare_scores(white_scores[0], white_scores[1], chess.WHITE) > 0
    assert compare_scores(black_scores[0], black_scores[1], chess.BLACK) > 0
    assert gap_to_best(white_scores[0], white_scores[1], chess.WHITE) == pytest.approx(0.24)
    assert gap_to_best(black_scores[0], black_scores[1], chess.BLACK) == pytest.approx(0.50)
    assert gap_to_best(white_scores[0], white_scores[2], chess.WHITE) == 0.24
    assert gap_to_best(CpScore(kind="cp", value=0), CpScore(kind="cp", value=0), chess.WHITE) == 0.0


def test_mate_scores_keep_order_and_gap_null() -> None:
    white_mate = MateScore(kind="mate", winner="white", moves=3)
    black_mate = MateScore(kind="mate", winner="black", moves=2)
    cp_score = CpScore(kind="cp", value=500)

    validate_rank_order([white_mate, cp_score, black_mate], chess.WHITE)
    validate_rank_order([black_mate, cp_score, white_mate], chess.BLACK)

    assert gap_to_best(white_mate, cp_score, chess.WHITE) is None
    assert gap_to_best(black_mate, cp_score, chess.BLACK) is None
    assert compare_scores(white_mate, cp_score, chess.WHITE) > 0
    assert compare_scores(black_mate, cp_score, chess.BLACK) > 0


def test_invalid_ordering_is_rejected() -> None:
    with pytest.raises(InvalidCandidateOrdering):
        validate_rank_order([CpScore(kind="cp", value=10), CpScore(kind="cp", value=12)], chess.WHITE)

    with pytest.raises(InvalidCandidateOrdering):
        gap_to_best(CpScore(kind="cp", value=10), CpScore(kind="cp", value=12), chess.WHITE)


def test_bound_scores_are_rejected() -> None:
    class FakeBoundScore:
        def pov(self, _color: chess.Color) -> "FakeBoundScore":
            return self

        def mate(self) -> None:
            return None

        def score(self, mate_score: int | None = None) -> int | None:
            return 7

        def is_lowerbound(self) -> bool:
            return True

    with pytest.raises(InvalidCandidateScore):
        normalize_pov_score(FakeBoundScore())


def test_pawn_unit_helpers_round_trip() -> None:
    assert cp_to_pawn_units(24) == pytest.approx(0.24)
    assert serialize_pawn_units(24) == pytest.approx(0.24)
    assert pawn_units_to_cp(0.24) == 24
    assert deserialize_pawn_units("0.24") == 24


def test_normalize_and_compare_attaches_gaps() -> None:
    snapshot = CandidateSnapshot(
        depth=8,
        reports=[
            CandidateReport(rank=1, depth=8, score=CpScore(kind="cp", value=34), pv=["e2e4"]),
            CandidateReport(rank=2, depth=8, score=CpScore(kind="cp", value=10), pv=["d2d4"]),
        ]
    )

    ranked = normalize_and_compare(snapshot, chess.WHITE)

    assert ranked[0][0] == 1
    assert ranked[0][1] == CpScore(kind="cp", value=34)
    assert ranked[0][2] == 0.0
    assert ranked[1][2] == pytest.approx(0.24)
