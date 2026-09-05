"""Conservative MultiPV checkpoint selection over incremental reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import chess

from .models import CandidateReport, CandidateSnapshot, CpScore, MateScore
from .scores import (
    BoundCandidateScore,
    InvalidCandidateOrdering,
    InvalidCandidateScore,
    WhiteScore,
    compare_scores,
    normalize_pov_score,
)


class CandidateCollectError(RuntimeError):
    code = "CANDIDATE_COLLECT_ERROR"


class IncompleteCandidateSet(CandidateCollectError):
    code = "INCOMPLETE_CANDIDATE_SET"

    def __init__(
        self,
        message: str = "no complete candidate checkpoint was accepted",
        *,
        diagnostics: "CollectorDiagnostics | None" = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or CollectorDiagnostics()


@dataclass
class CollectorDiagnostics:
    incomplete_groups: int = 0
    bound_only_groups: int = 0
    duplicate_root_groups: int = 0
    inconsistent_groups: int = 0


@dataclass(frozen=True)
class CollectedSnapshot:
    snapshot: CandidateSnapshot | None
    diagnostics: CollectorDiagnostics


def _value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _is_candidate_like(record: Any) -> bool:
    keys = ("rank", "multipv", "score", "pv")
    if isinstance(record, Mapping):
        return any(key in record for key in keys)
    return any(hasattr(record, key) for key in keys)


def _root_side(board: chess.Board) -> chess.Color:
    return chess.WHITE if board.turn == chess.WHITE else chess.BLACK


def _root_move_count(board: chess.Board) -> int:
    return sum(1 for _ in board.legal_moves)


def _move_uci(move: Any) -> str:
    if isinstance(move, str):
        return move
    if hasattr(move, "uci"):
        uci = move.uci
        return uci() if callable(uci) else str(uci)
    raise InvalidCandidateScore("principal variation contains a non-move entry")


def _time_ms(record: Any) -> int | None:
    raw = _value(record, "time_ms")
    if raw is None:
        raw = _value(record, "time")
        if raw is None:
            return None
        try:
            return int(round(float(raw) * 1000))
        except (TypeError, ValueError) as exc:
            raise InvalidCandidateScore("candidate report time is invalid") from exc
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidCandidateScore("candidate report time is invalid") from exc


def _coerce_rank(record: Any, limit: int) -> int | None:
    rank = _value(record, "rank")
    if rank is None:
        rank = _value(record, "multipv")
    if rank is None and limit == 1:
        return 1
    if rank is None:
        return None
    try:
        rank_int = int(rank)
    except (TypeError, ValueError):
        return None
    if limit == 1 and rank_int != 1:
        return None
    return rank_int if rank_int >= 1 else None


def _coerce_score(record: Any) -> WhiteScore:
    raw_score = _value(record, "score")
    if raw_score is None:
        raise InvalidCandidateScore("candidate report is missing a score")
    if isinstance(raw_score, (CpScore, MateScore)):
        return raw_score
    if isinstance(raw_score, int) and not isinstance(raw_score, bool):
        return CpScore(kind="cp", value=int(raw_score))
    if hasattr(raw_score, "winner") and hasattr(raw_score, "moves"):
        winner = getattr(raw_score, "winner")
        moves = getattr(raw_score, "moves")
        if winner not in {"white", "black"}:
            raise InvalidCandidateScore("mate score winner must be white or black")
        return MateScore(kind="mate", winner=winner, moves=int(moves))
    tagged = normalize_pov_score(raw_score, info=record if isinstance(record, Mapping) else None)
    return tagged


def _coerce_report(record: Any, limit: int, root: chess.Board) -> CandidateReport:
    raw_score = _value(record, "score")

    # python-chess exposes bounds on the enclosing info packet, while our
    # typed/int fast paths intentionally do not inspect that packet.  Reject
    # those top-level flags before any score coercion so a bounded score can
    # never be mistaken for an exact checkpoint.
    if isinstance(record, Mapping) and (record.get("lowerbound") or record.get("upperbound")):
        raise BoundCandidateScore("candidate score is bounded")
    for name in ("lowerbound", "upperbound"):
        value = getattr(record, name, None)
        if value is not None and bool(value() if callable(value) else value):
            raise BoundCandidateScore("candidate score is bounded")

    rank = _coerce_rank(record, limit)
    if rank is None:
        raise InvalidCandidateScore("candidate report is missing a rank")

    depth = _value(record, "depth")
    if depth is None:
        raise InvalidCandidateScore("candidate report is missing a depth")
    try:
        depth_int = int(depth)
    except (TypeError, ValueError) as exc:
        raise InvalidCandidateScore("candidate report depth is invalid") from exc
    if depth_int < 1:
        raise InvalidCandidateScore("candidate report depth must be at least 1")

    pv_raw = _value(record, "pv")
    if not pv_raw:
        raise InvalidCandidateScore("candidate report is missing a principal variation")
    pv = [_move_uci(move) for move in pv_raw]
    if not pv:
        raise InvalidCandidateScore("candidate report is missing a principal variation")

    first_move = chess.Move.from_uci(pv[0])
    if not root.is_legal(first_move):
        raise InvalidCandidateScore("candidate report starts with an illegal root move")

    if limit > 1 and _value(record, "multipv") is None:
        raise InvalidCandidateScore("candidate report is missing explicit multipv metadata")

    score = _coerce_score(record)

    payload: dict[str, Any] = {
        "rank": rank,
        "depth": depth_int,
        "score": score,
        "pv": pv,
    }
    if (seldepth := _value(record, "seldepth")) is not None:
        payload["seldepth"] = seldepth
    if (nodes := _value(record, "nodes")) is not None:
        payload["nodes"] = nodes
    time_ms = _time_ms(record)
    if time_ms is not None:
        payload["time_ms"] = time_ms
    return CandidateReport.model_validate(payload)


def _complete_snapshot(depth: int, reports: list[CandidateReport]) -> CandidateSnapshot:
    snapshot = CandidateSnapshot.model_validate({"depth": depth, "reports": reports})
    return snapshot.model_copy(deep=True)


def collect_reports(
    reports: Any,
    root: chess.Board,
    target_count: int,
) -> CandidateSnapshot:
    """Collect the latest complete rank-ordered report group from a stream."""

    if target_count < 1:
        raise ValueError("target_count must be positive")

    collected = collect_reports_with_diagnostics(reports, root, target_count)
    if collected.snapshot is None:
        raise IncompleteCandidateSet(diagnostics=collected.diagnostics)
    return collected.snapshot


def _reset_state(
    provisional: list[CandidateReport],
    seen_root_moves: set[str],
) -> None:
    provisional.clear()
    seen_root_moves.clear()


def collect_reports_with_diagnostics(
    reports: Any,
    root: chess.Board,
    target_count: int,
) -> CollectedSnapshot:
    if target_count < 1:
        raise ValueError("target_count must be positive")

    legal_root_moves = _root_move_count(root)
    if legal_root_moves == 0:
        raise ValueError("collect_reports only handles nonterminal roots")

    limit = min(target_count, legal_root_moves)
    root_side = _root_side(root)
    diagnostics = CollectorDiagnostics()

    last_complete: CandidateSnapshot | None = None
    provisional: list[CandidateReport] = []
    provisional_depth: int | None = None
    expected_rank = 1
    seen_root_moves: set[str] = set()

    for raw in reports:
        if not _is_candidate_like(raw):
            continue

        rank = _coerce_rank(raw, limit)
        if rank is None:
            if provisional:
                diagnostics.incomplete_groups += 1
                _reset_state(provisional, seen_root_moves)
                provisional_depth = None
                expected_rank = 1
            continue

        is_rank_one = rank == 1
        if is_rank_one and provisional:
            diagnostics.incomplete_groups += 1
            _reset_state(provisional, seen_root_moves)
            provisional_depth = None
            expected_rank = 1

        if is_rank_one:
            provisional_depth = None

        try:
            report = _coerce_report(raw, limit, root)
        except BoundCandidateScore:
            diagnostics.bound_only_groups += 1
            _reset_state(provisional, seen_root_moves)
            provisional_depth = None
            expected_rank = 1
            continue
        except (InvalidCandidateScore, InvalidCandidateOrdering, ValueError):
            if provisional:
                diagnostics.inconsistent_groups += 1
                _reset_state(provisional, seen_root_moves)
                provisional_depth = None
                expected_rank = 1
            continue

        if limit == 1:
            last_complete = _complete_snapshot(report.depth, [report])
            provisional_depth = report.depth
            continue

        if not provisional:
            if is_rank_one:
                provisional = [report]
                provisional_depth = report.depth
                expected_rank = 2
                seen_root_moves.add(report.pv[0])
            continue

        if report.rank != expected_rank or report.depth != provisional_depth:
            diagnostics.inconsistent_groups += 1
            _reset_state(provisional, seen_root_moves)
            provisional_depth = None
            expected_rank = 1
            continue

        root_move = report.pv[0]
        if root_move in seen_root_moves:
            diagnostics.duplicate_root_groups += 1
            _reset_state(provisional, seen_root_moves)
            provisional_depth = None
            expected_rank = 1
            continue

        if compare_scores(report.score, provisional[-1].score, root_side) > 0:
            diagnostics.inconsistent_groups += 1
            _reset_state(provisional, seen_root_moves)
            provisional_depth = None
            expected_rank = 1
            continue

        provisional.append(report)
        seen_root_moves.add(root_move)
        expected_rank += 1

        if len(provisional) == limit:
            last_complete = _complete_snapshot(provisional_depth or report.depth, provisional)
            _reset_state(provisional, seen_root_moves)
            provisional_depth = None
            expected_rank = 1

    if provisional:
        diagnostics.incomplete_groups += 1
    return CollectedSnapshot(snapshot=last_complete, diagnostics=diagnostics)


__all__ = [
    "CandidateCollectError",
    "CandidateReport",
    "CandidateSnapshot",
    "CollectedSnapshot",
    "CollectorDiagnostics",
    "IncompleteCandidateSet",
    "collect_reports",
    "collect_reports_with_diagnostics",
]
