"""Build and validate complete candidate bundles from one engine snapshot."""

from __future__ import annotations

import time
from typing import Literal

import chess

from .models import (
    AnalysisMetadata,
    CandidateMove,
    CandidateReport,
    CandidateResult,
    CandidateScore,
    CandidateSnapshot,
    MateDisplay,
    Provenance,
)
from .replay import replay_candidate
from .validate import CandidateResultValidationError, validate_candidate_result


def display_score(score: CandidateScore) -> tuple[float | None, MateDisplay | None]:
    if score.kind == "cp":
        return score.value / 100, None
    return None, MateDisplay(winner=score.winner, moves=score.moves)


def root_relative_gap(best: CandidateScore, candidate: CandidateScore, root_side: str) -> float | None:
    if best.kind != "cp" or candidate.kind != "cp":
        return None
    sign = 1 if root_side == "white" else -1
    gap = sign * (best.value - candidate.value) / 100
    if gap < 0:
        raise CandidateResultValidationError("candidate score ordering produced a negative gap")
    return gap


def build_candidate_move(
    root: chess.Board,
    report: CandidateReport,
    best_score: CandidateScore,
    root_side: Literal["white", "black"],
    max_continuation_plies: int = 6,
    timing_sink: dict[str, int] | None = None,
) -> CandidateMove:
    replay_started = time.monotonic()
    replay = replay_candidate(root, report, max_continuation_plies)
    if timing_sink is not None:
        timing_sink["replay_ms"] = timing_sink.get("replay_ms", 0) + max(
            0, int(round((time.monotonic() - replay_started) * 1000))
        )
    root_move = chess.Move.from_uci(report.pv[0])
    evaluation, mate = display_score(report.score)
    try:
        from ..explanations.candidates import CandidateRenderError, describe_candidate

        san = root.san(root_move)
        render_started = time.monotonic()
        description = describe_candidate(replay, replay.root_snapshot.facts)
        if timing_sink is not None:
            timing_sink["render_ms"] = timing_sink.get("render_ms", 0) + max(
                0, int(round((time.monotonic() - render_started) * 1000))
            )
    except CandidateRenderError:
        raise
    except Exception as exc:
        raise CandidateRenderError(str(exc)) from exc
    return CandidateMove(
        rank=report.rank,
        move={"uci": root_move.uci(), "san": san},
        evaluation=evaluation,
        mate=mate,
        gap_to_best=root_relative_gap(best_score, report.score, root_side),
        continuation=replay.continuation,
        continuation_end=replay.continuation_end,
        material_delta=replay.material_delta,
        description=description.description,
        description_sources=description.description_sources,
    )


def _terminal_analysis(root: chess.Board, requested_count: int, search_budget_ms: int, max_plies: int) -> AnalysisMetadata:
    return AnalysisMetadata(
        facts_version=1,
        root_side="white" if root.turn == chess.WHITE else "black",
        requested_count=requested_count,
        returned_count=0,
        snapshot_depth=None,
        search_budget_ms=search_budget_ms,
        max_continuation_plies=max_plies,
        selection_policy="terminal_position",
    )


def _snapshot_analysis(
    root: chess.Board,
    snapshot: CandidateSnapshot,
    requested_count: int,
    search_budget_ms: int,
    max_plies: int,
) -> AnalysisMetadata:
    return AnalysisMetadata(
        facts_version=1,
        root_side="white" if root.turn == chess.WHITE else "black",
        requested_count=requested_count,
        returned_count=len(snapshot.reports),
        snapshot_depth=snapshot.depth,
        search_budget_ms=search_budget_ms,
        max_continuation_plies=max_plies,
        selection_policy="last_complete_depth",
    )


def build_candidate_result(
    root: chess.Board,
    snapshot: CandidateSnapshot | None,
    provenance: Provenance,
    *,
    requested_count: int = 3,
    search_budget_ms: int = 3000,
    max_continuation_plies: int = 6,
    timing_sink: dict[str, int] | None = None,
) -> CandidateResult:
    """Construct a candidate result, including the explicit terminal case."""

    if snapshot is None:
        analysis = _terminal_analysis(root, requested_count, search_budget_ms, max_continuation_plies)
        moves: list[CandidateMove] = []
    else:
        reports = snapshot.reports
        if not reports:
            raise CandidateResultValidationError("candidate snapshot is incomplete")
        if any(report.depth != snapshot.depth for report in reports):
            raise CandidateResultValidationError("candidate reports must share the snapshot depth")
        expected_count = min(requested_count, sum(1 for _ in root.legal_moves))
        if len(reports) != expected_count:
            raise CandidateResultValidationError("candidate count does not match legal root move count")
        if [report.rank for report in reports] != list(range(1, len(reports) + 1)):
            raise CandidateResultValidationError("candidate report ranks must be contiguous")
        if provenance.selected_depth != snapshot.depth:
            raise CandidateResultValidationError("provenance selected depth disagrees with snapshot")
        expected_scores = [report.score for report in reports]
        if provenance.raw_scores != expected_scores:
            raise CandidateResultValidationError("provenance raw scores disagree with reports")
        expected_lengths = [len(report.pv) for report in reports]
        if provenance.original_pv_lengths != expected_lengths:
            raise CandidateResultValidationError("provenance PV lengths disagree with reports")
        if len({report.pv[0] for report in reports}) != len(reports):
            raise CandidateResultValidationError("candidate root moves must be distinct")
        best_score = reports[0].score
        root_side = "white" if root.turn == chess.WHITE else "black"
        moves = [
            build_candidate_move(root, report, best_score, root_side, max_continuation_plies, timing_sink)
            for report in reports
        ]
        analysis = _snapshot_analysis(
            root, snapshot, requested_count, search_budget_ms, max_continuation_plies
        )
    try:
        result = CandidateResult(version=1, analysis=analysis, moves=moves, provenance=provenance)
    except Exception as exc:
        raise CandidateResultValidationError(str(exc)) from exc
    return validate_candidate_result(result, root)


def rank_one_score(result: CandidateResult) -> CandidateScore | None:
    """Return the selected rank-one raw score, or ``None`` for a terminal root."""

    if not result.moves:
        return None
    return result.provenance.raw_scores[0]


def rank_one_display(result: CandidateResult) -> tuple[float | None, MateDisplay | None]:
    score = rank_one_score(result)
    return (None, None) if score is None else display_score(score)


__all__ = [
    "build_candidate_move",
    "build_candidate_result",
    "display_score",
    "rank_one_display",
    "rank_one_score",
    "root_relative_gap",
]
