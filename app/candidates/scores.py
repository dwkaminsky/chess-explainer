"""White-oriented score normalization and comparison helpers."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, Mapping, TypeAlias

import chess

from app.stockfish import MateResult, normalize_score as normalize_engine_score
from .models import CandidateReport, CandidateScore, CandidateSnapshot, CpScore, MateScore


class InvalidCandidateScore(ValueError):
    code = "INVALID_CANDIDATE_SCORE"


class BoundCandidateScore(InvalidCandidateScore):
    code = "INVALID_CANDIDATE_BOUND_SCORE"


class InvalidCandidateOrdering(ValueError):
    code = "INVALID_CANDIDATE_ORDERING"

WhiteScore: TypeAlias = CpScore | MateScore
NormalizedScore: TypeAlias = tuple[int, WhiteScore, float | None]


def _root_side_value(root_side: chess.Color | Literal["white", "black"]) -> chess.Color:
    if root_side in (chess.WHITE, "white"):
        return chess.WHITE
    if root_side in (chess.BLACK, "black"):
        return chess.BLACK
    raise ValueError("root side must be white or black")


def _score_kind(score: Any) -> str | None:
    return getattr(score, "kind", None) or (score.get("kind") if isinstance(score, Mapping) else None)


def _score_field(score: Any, name: str) -> Any:
    if isinstance(score, Mapping):
        return score.get(name)
    return getattr(score, name, None)


def _is_bound_score(score: Any, info: Mapping[str, Any] | None = None) -> bool:
    if info is not None and (info.get("lowerbound") or info.get("upperbound")):
        return True
    if isinstance(score, Mapping):
        return bool(score.get("lowerbound") or score.get("upperbound"))
    for name in ("is_lowerbound", "is_upperbound", "lowerbound", "upperbound"):
        value = getattr(score, name, None)
        if value is None:
            continue
        try:
            if value() if callable(value) else bool(value):
                return True
        except Exception:
            continue
    return False


def _coerce_tagged_score(score: Any) -> WhiteScore | None:
    if isinstance(score, (CpScore, MateScore)):
        return score
    kind = _score_kind(score)
    if kind == "cp":
        cp = _score_field(score, "value")
        if cp is None:
            cp = _score_field(score, "cp")
        if cp is None:
            raise InvalidCandidateScore("cp score is missing its centipawn value")
        return CpScore(kind="cp", value=int(cp))
    if kind == "mate":
        moves = _score_field(score, "moves")
        if moves is None:
            moves = _score_field(score, "mate")
        winner = _score_field(score, "winner")
        if moves is None or winner is None:
            raise InvalidCandidateScore("mate score is missing winner or distance")
        if winner not in {"white", "black"}:
            raise InvalidCandidateScore("mate score winner must be white or black")
        return MateScore(kind="mate", winner=winner, moves=int(moves))
    return None


def normalize_pov_score(score: Any, info: Mapping[str, Any] | None = None) -> WhiteScore:
    """Normalize a python-chess score into a white-oriented tagged score."""

    if _is_bound_score(score, info):
        raise BoundCandidateScore("candidate score is bounded")
    if isinstance(score, MateResult):
        return MateScore(kind="mate", winner=score.winner, moves=int(score.moves))
    if isinstance(score, int) and not isinstance(score, bool):
        return CpScore(kind="cp", value=int(score))

    tagged = _coerce_tagged_score(score)
    if tagged is not None:
        return tagged

    payload: dict[str, Any] = {"score": score}
    if info is not None:
        payload.update(info)

    try:
        normalized = normalize_engine_score(payload)
    except BoundCandidateScore:
        raise
    except Exception as exc:  # stockfish raises EngineError subclasses for invalid data
        raise InvalidCandidateScore(str(exc) or "candidate score is invalid") from exc
    if isinstance(normalized, MateResult):
        return MateScore(kind="mate", winner=normalized.winner, moves=int(normalized.moves))
    return CpScore(kind="cp", value=int(normalized))


normalize_score = normalize_pov_score


def cp_to_pawn_units(cp: int) -> float:
    return cp / 100.0


def pawn_units_to_cp(pawns: float | Decimal | str | int) -> int:
    return int((Decimal(str(pawns)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


serialize_pawn_units = cp_to_pawn_units
deserialize_pawn_units = pawn_units_to_cp


def score_key(score: WhiteScore, root_side: chess.Color | Literal["white", "black"]) -> tuple[int, int]:
    side = _root_side_value(root_side)
    if side == chess.WHITE:
        if score.kind == "mate":
            return (2, -score.moves) if score.winner == "white" else (0, score.moves)
        return (1, score.value)
    if score.kind == "mate":
        return (2, -score.moves) if score.winner == "black" else (0, score.moves)
    return (1, -score.value)


def compare_scores(
    left: WhiteScore,
    right: WhiteScore,
    root_side: chess.Color | Literal["white", "black"],
) -> int:
    """Return 1 when left is better, -1 when right is better, and 0 on ties."""

    left_key = score_key(left, root_side)
    right_key = score_key(right, root_side)
    if left_key > right_key:
        return 1
    if left_key < right_key:
        return -1
    return 0


def validate_rank_order(
    reports: Any,
    root_side: chess.Color | Literal["white", "black"],
) -> None:
    """Ensure a sequence is non-increasing in engine order for the root side."""

    previous: WhiteScore | None = None
    for item in reports:
        score = _score_field(item, "score") if _score_field(item, "score") is not None else item
        tagged = normalize_pov_score(score)
        if previous is not None and compare_scores(tagged, previous, root_side) > 0:
            raise InvalidCandidateOrdering("candidate scores are out of rank order")
        previous = tagged


def gap_to_best(
    best: WhiteScore,
    score: WhiteScore,
    root_side: chess.Color | Literal["white", "black"],
) -> float | None:
    """Compute the nonnegative gap to best in pawn units, or None for mates."""

    best_tagged = normalize_pov_score(best)
    score_tagged = normalize_pov_score(score)
    if best_tagged.kind == "mate" or score_tagged.kind == "mate":
        return None

    side = _root_side_value(root_side)
    gap_cp = (
        best_tagged.value - score_tagged.value
        if side == chess.WHITE
        else score_tagged.value - best_tagged.value
    )
    if gap_cp < 0:
        raise InvalidCandidateOrdering("candidate score ordering is inconsistent")
    return cp_to_pawn_units(gap_cp)


def normalize_and_compare(
    snapshot: Any,
    root_side: chess.Color | Literal["white", "black"],
) -> list[NormalizedScore]:
    """Normalize a snapshot's scores and attach gaps to rank one."""

    reports = getattr(snapshot, "reports", None)
    if reports is None and isinstance(snapshot, Mapping):
        reports = snapshot.get("reports")
    if reports is None:
        raise ValueError("snapshot must expose reports")

    normalized_reports: list[WhiteScore] = []
    for report in reports:
        score = _score_field(report, "score") if _score_field(report, "score") is not None else report
        normalized_reports.append(normalize_pov_score(score))

    validate_rank_order(normalized_reports, root_side)
    if not normalized_reports:
        return []

    best = normalized_reports[0]
    return [
        (index + 1, score, gap_to_best(best, score, root_side))
        for index, score in enumerate(normalized_reports)
    ]


__all__ = [
    "InvalidCandidateOrdering",
    "InvalidCandidateScore",
    "NormalizedScore",
    "WhiteScore",
    "compare_scores",
    "cp_to_pawn_units",
    "deserialize_pawn_units",
    "gap_to_best",
    "normalize_and_compare",
    "normalize_pov_score",
    "normalize_score",
    "pawn_units_to_cp",
    "score_key",
    "serialize_pawn_units",
    "validate_rank_order",
]
