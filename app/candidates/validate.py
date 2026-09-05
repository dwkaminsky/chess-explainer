"""Whole-bundle validation requiring the submitted root board."""

from __future__ import annotations

import json

import chess

from ..facts.extract import terminal_context_from_board
from .models import CandidateResult, CandidateScore, CpScore, MateScore
from .models import CandidateReport
from .replay import ReplayError, replay_candidate


class CandidateResultValidationError(ValueError):
    code = "CANDIDATE_RESULT_INVALID"


def _fail(message: str) -> None:
    raise CandidateResultValidationError(message)


def _score_key(score: CandidateScore, root_side: str) -> tuple[int, int]:
    if score.kind == "mate":
        winning = score.winner == root_side
        return (2, -score.moves) if winning else (0, score.moves)
    return (1, score.value if root_side == "white" else -score.value)


def _validate_sources(move) -> None:
    if not move.description_sources:
        _fail("candidate description requires sentence provenance")
    if len(move.description_sources) > 3:
        _fail("too many candidate description sentences")
    for source in move.description_sources:
        if source.sentence_index < 0 or source.sentence_index >= len(move.description_sources):
            _fail("description source sentence index is out of range")
        for ply_index in source.plies:
            if ply_index < 1 or ply_index > len(move.continuation):
                _fail("description source ply is not retained")
        cited_change_ids = {
            change.id
            for ply_index in source.plies
            for change in move.continuation[ply_index - 1].changes
        }
        if not set(source.change_ids).issubset(cited_change_ids):
            _fail("description source references a change outside its cited plies")
    if {source.sentence_index for source in move.description_sources} != set(range(len(move.description_sources))):
        _fail("description source indices must be contiguous")
    if len(move.description.split()) > 90:
        _fail("candidate description exceeds 90 words")
    if len(move.description.encode("utf-8")) >= 256 * 1024:
        _fail("candidate result exceeds 256 KiB")
    forbidden = ("wins material", "wins a pawn", "forces", "only move", "refutes", "because stockfish", "controls", "prepares")
    lowered = move.description.casefold()
    if any(phrase in lowered for phrase in forbidden):
        _fail("candidate description contains an unsupported strategic claim")


def validate_candidate_result(result: CandidateResult, root: chess.Board) -> CandidateResult:
    """Validate cross-field and root-board invariants before persistence."""

    if result.version != 1 or result.analysis.facts_version != 1:
        _fail("unsupported candidate or facts version")
    root_side = "white" if root.turn == chess.WHITE else "black"
    if result.analysis.root_side != root_side:
        _fail("analysis root side does not match the submitted board")
    if result.provenance.normalized_fen != root.fen(en_passant="fen"):
        _fail("provenance FEN does not match the submitted board")
    if result.analysis.returned_count != len(result.moves):
        _fail("analysis returned count does not match candidates")
    if result.provenance.selected_depth != result.analysis.snapshot_depth:
        _fail("selected depth does not match analysis snapshot depth")
    if len(result.provenance.raw_scores) != len(result.moves):
        _fail("raw score count does not match candidates")
    if len(result.provenance.original_pv_lengths) != len(result.moves):
        _fail("PV length count does not match candidates")

    root_terminal = terminal_context_from_board(root).kind != "none"
    if not result.moves:
        if not root_terminal or result.analysis.selection_policy != "terminal_position":
            _fail("only a terminal root may return an empty candidate list")
        if result.analysis.snapshot_depth is not None or result.provenance.selected_depth is not None:
            _fail("terminal candidates cannot have a selected depth")
    else:
        if root_terminal or result.analysis.selection_policy != "last_complete_depth":
            _fail("nonterminal candidates require last_complete_depth policy")
        expected_count = min(result.analysis.requested_count, sum(1 for _ in root.legal_moves))
        if len(result.moves) != expected_count:
            _fail("candidate count does not match legal root move count")
        if [move.rank for move in result.moves] != list(range(1, len(result.moves) + 1)):
            _fail("candidate ranks must be contiguous")
        if len({move.move.uci for move in result.moves}) != len(result.moves):
            _fail("candidate root moves must be distinct")

    for index, move in enumerate(result.moves):
        try:
            root_move = chess.Move.from_uci(move.move.uci)
        except ValueError as exc:
            _fail(f"candidate root UCI is malformed: {exc}")
        if root_move not in root.legal_moves:
            _fail("candidate root move is not legal on the submitted board")
        if not move.continuation or move.continuation[0].uci != move.move.uci:
            _fail("continuation must begin with the candidate root move")
        if result.provenance.original_pv_lengths[index] < len(move.continuation):
            _fail("retained continuation exceeds original PV length")
        raw_score = result.provenance.raw_scores[index]
        try:
            san = root.san(root_move)
        except Exception as exc:
            _fail(f"candidate root move cannot produce SAN: {exc}")
        if move.move.san != san:
            _fail("candidate SAN disagrees with the submitted board")
        if index and _score_key(result.provenance.raw_scores[index - 1], root_side) < _score_key(raw_score, root_side):
            _fail("raw scores are not in root-player rank order")
        best_score = result.provenance.raw_scores[0]
        if best_score.kind == "mate" or raw_score.kind == "mate":
            expected_gap = None
        else:
            expected_gap = (
                (best_score.value - raw_score.value) if root_side == "white" else (raw_score.value - best_score.value)
            ) / 100
            if expected_gap < 0:
                _fail("raw score ordering produced a negative gap")
        if move.gap_to_best != expected_gap:
            _fail("candidate gap_to_best disagrees with rank-one score")
        if isinstance(raw_score, CpScore):
            if move.evaluation is None or move.mate is not None:
                _fail("ordinary raw score must produce an ordinary candidate score")
            if abs(move.evaluation - raw_score.value / 100) > 1e-9:
                _fail("candidate evaluation disagrees with raw score")
        elif isinstance(raw_score, MateScore):
            if move.mate is None or move.evaluation is not None:
                _fail("mate raw score must produce a mate candidate score")
            if (move.mate.winner, move.mate.moves) != (raw_score.winner, raw_score.moves):
                _fail("candidate mate disagrees with raw score")
        _validate_sources(move)

        try:
            replayed = replay_candidate(
                root,
                CandidateReport(
                    rank=move.rank,
                    depth=result.analysis.snapshot_depth or 1,
                    score=raw_score,
                    pv=[ply.uci for ply in move.continuation],
                ),
                max_plies=len(move.continuation),
                expected_move=move.move.uci,
            )
        except Exception as exc:
            _fail(f"retained continuation does not replay: {exc}")
        persisted = [ply.model_dump(mode="json", by_alias=True) for ply in move.continuation]
        replayed_dump = [ply.model_dump(mode="json", by_alias=True) for ply in replayed.continuation]
        if persisted != replayed_dump:
            _fail("retained continuation differs from legal replay")
        expected_end = (
            "terminal"
            if replayed.continuation_end == "terminal"
            else "prefix_limit"
            if result.provenance.original_pv_lengths[index] > len(move.continuation)
            else "engine_line_end"
        )
        if move.continuation_end != expected_end:
            _fail("continuation ending reason is inconsistent")
        if replayed.material_delta != move.material_delta:
            _fail("candidate endpoint material delta disagrees with replay")

        # Descriptions are persisted output, not client-supplied annotations.
        # Re-render from the exact replayed state so arbitrary prose, stale
        # templates, and mismatched provenance cannot enter the durable bundle.
        try:
            from ..explanations.candidates import describe_candidate

            expected_description = describe_candidate(
                replayed, replayed.root_snapshot.facts
            )
        except Exception as exc:
            _fail(f"candidate description cannot be rendered: {exc}")
        if move.description != expected_description.description:
            _fail("candidate description does not match the deterministic renderer")
        actual_sources = [
            source.model_dump(mode="json") for source in move.description_sources
        ]
        expected_sources = [
            source.model_dump(mode="json")
            for source in expected_description.description_sources
        ]
        if actual_sources != expected_sources:
            _fail("candidate description provenance does not match the deterministic renderer")

    try:
        serialized = result.model_dump(mode="json", by_alias=True)
        encoded = json.dumps(serialized, separators=(",", ":")).encode("utf-8")
    except Exception as exc:
        _fail(f"candidate result cannot be serialized: {exc}")
    if len(encoded) > 256 * 1024:
        _fail("candidate result exceeds 256 KiB")
    return result


__all__ = ["CandidateResultValidationError", "validate_candidate_result"]
