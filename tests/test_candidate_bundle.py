from __future__ import annotations

import chess
import pytest
from pydantic import ValidationError

from app.candidates import (
    AnalysisMetadata,
    CandidateReport,
    CandidateResultValidationError,
    CandidateSnapshot,
    CpScore,
    MateScore,
    Provenance,
    build_candidate_result,
    rank_one_display,
    root_relative_gap,
    validate_candidate_result,
)


ROOT_FEN = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2"


def _report(rank: int, move: str, score: int) -> CandidateReport:
    return CandidateReport(rank=rank, depth=10, score=CpScore(kind="cp", value=score), pv=[move])


def _provenance(root_fen: str = ROOT_FEN, *, scores=None, lengths=None, depth=10) -> Provenance:
    return Provenance(
        normalized_fen=root_fen,
        engine_build="stockfish-test",
        network_hash=None,
        options={"Threads": 1, "Hash": 64, "MultiPV": 3},
        selected_depth=depth,
        raw_scores=scores if scores is not None else [CpScore(kind="cp", value=34)],
        original_pv_lengths=lengths if lengths is not None else [1],
        elapsed_attempt_ms=1,
        elapsed_search_ms=1,
        elapsed_replay_ms=0,
        elapsed_render_ms=0,
        incomplete_groups=0,
        bound_only_groups=0,
        duplicate_root_groups=0,
        inconsistent_groups=0,
    )


def test_build_bundle_is_deterministic_and_qualifies_recapture():
    snapshot = CandidateSnapshot(
        depth=10,
        reports=[
            CandidateReport(
                rank=1,
                depth=10,
                score=CpScore(kind="cp", value=34),
                pv=["e4d5", "d8d5"],
            )
        ],
    )
    provenance = _provenance(lengths=[2])
    first = build_candidate_result(chess.Board(ROOT_FEN), snapshot, provenance, requested_count=1)
    second = build_candidate_result(chess.Board(ROOT_FEN), snapshot, provenance, requested_count=1)
    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
    assert first.moves[0].description == (
        "exd5 captures Black's d5-pawn and makes the e-file semi-open for White. "
        "In the displayed continuation, 2...Qxd5 captures White's pawn on d5, restoring equal pawn counts."
    )
    assert first.moves[0].description_sources[0].plies == [1]


def test_renderer_prioritizes_late_recapture_of_root_mover():
    fen = "r2q2k1/8/8/3p4/4P3/8/P5P1/6K1 w - - 0 1"
    snapshot = CandidateSnapshot(
        depth=10,
        reports=[
            CandidateReport(
                rank=1,
                depth=10,
                score=CpScore(kind="cp", value=34),
                pv=["e4d5", "a8a2", "g2g3", "d8d5"],
            )
        ],
    )
    result = build_candidate_result(
        chess.Board(fen),
        snapshot,
        _provenance(fen, lengths=[4]),
        requested_count=1,
    )

    move = result.moves[0]
    assert "Qxd5 captures White's pawn on d5" in move.description
    assert "Rxa2 captures" not in move.description
    assert move.description_sources[1].plies == [4]


def test_black_root_gap_is_root_player_relative():
    assert root_relative_gap(CpScore(kind="cp", value=-40), CpScore(kind="cp", value=10), "black") == 0.5
    assert root_relative_gap(CpScore(kind="cp", value=34), CpScore(kind="cp", value=10), "white") == 0.24
    assert root_relative_gap(MateScore(kind="mate", winner="white", moves=2), CpScore(kind="cp", value=10), "white") is None


def test_black_root_replay_preserves_side_and_fullmove_notation():
    fen = "rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2"
    snapshot = CandidateSnapshot(
        depth=10,
        reports=[CandidateReport(rank=1, depth=10, score=CpScore(kind="cp", value=-40), pv=["d5d4", "g1f3"])],
    )
    result = build_candidate_result(
        chess.Board(fen), snapshot,
        _provenance(fen, scores=[CpScore(kind="cp", value=-40)], lengths=[2]),
        requested_count=1,
    )
    assert result.moves[0].continuation[0].side == "black"
    assert result.moves[0].continuation[0].move_number == 2


def test_mate_candidate_has_display_mate_and_null_gap():
    fen = "4k3/8/8/8/8/8/4K3/6R1 w - - 0 1"
    snapshot = CandidateSnapshot(
        depth=10,
        reports=[CandidateReport(rank=1, depth=10, score=MateScore(kind="mate", winner="white", moves=1), pv=["g1g8"])],
    )
    result = build_candidate_result(
        chess.Board(fen), snapshot,
        _provenance(fen, scores=[MateScore(kind="mate", winner="white", moves=1)], lengths=[1]),
        requested_count=1,
    )
    assert result.moves[0].evaluation is None
    assert result.moves[0].mate is not None
    assert result.moves[0].gap_to_best is None
    assert rank_one_display(result)[1].moves == 1


def test_terminal_bundle_is_explicit_and_empty():
    fen = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
    provenance = _provenance(fen, scores=[], lengths=[], depth=None)
    result = build_candidate_result(chess.Board(fen), None, provenance)
    assert result.moves == []
    assert result.analysis.selection_policy == "terminal_position"
    assert result.analysis.snapshot_depth is None


def test_validation_rejects_unresolved_sources_and_long_description():
    root = chess.Board(ROOT_FEN)
    snapshot = CandidateSnapshot(depth=10, reports=[_report(1, "b1c3", 34)])
    result = build_candidate_result(root, snapshot, _provenance(lengths=[1]), requested_count=1)
    move = result.moves[0].model_copy(update={"description": "word " * 91})
    malformed = result.model_copy(update={"moves": [move]})
    with pytest.raises(CandidateResultValidationError):
        validate_candidate_result(malformed, root)


def test_validation_rejects_arbitrary_prose_even_with_valid_shape():
    root = chess.Board(ROOT_FEN)
    snapshot = CandidateSnapshot(depth=10, reports=[_report(1, "e4d5", 34)])
    result = build_candidate_result(root, snapshot, _provenance(lengths=[1]), requested_count=1)
    move = result.moves[0].model_copy(update={"description": "This arbitrary sentence is not rendered from the replay."})
    with pytest.raises(CandidateResultValidationError, match="deterministic renderer"):
        validate_candidate_result(result.model_copy(update={"moves": [move]}), root)


def test_validation_rejects_cross_ply_description_provenance():
    root = chess.Board(ROOT_FEN)
    snapshot = CandidateSnapshot(
        depth=10,
        reports=[
            CandidateReport(
                rank=1,
                depth=10,
                score=CpScore(kind="cp", value=34),
                pv=["e4d5", "d8d5"],
            )
        ],
    )
    result = build_candidate_result(root, snapshot, _provenance(lengths=[2]), requested_count=1)
    first_source, second_source = result.moves[0].description_sources
    first_source = first_source.model_copy(update={"change_ids": second_source.change_ids})
    move = result.moves[0].model_copy(
        update={"description_sources": [first_source, second_source]}
    )
    with pytest.raises(CandidateResultValidationError, match="outside its cited plies"):
        validate_candidate_result(result.model_copy(update={"moves": [move]}), root)


def test_bad_candidate_analysis_is_reported_as_result_invalid():
    root = chess.Board(ROOT_FEN)
    snapshot = CandidateSnapshot(depth=10, reports=[_report(1, "b1c3", 34)])
    with pytest.raises(CandidateResultValidationError):
        build_candidate_result(root, snapshot, _provenance(lengths=[2]), requested_count=1)


def test_validation_replays_and_rejects_tampered_fen_or_material_delta():
    root = chess.Board(ROOT_FEN)
    snapshot = CandidateSnapshot(depth=10, reports=[_report(1, "b1c3", 34)])
    result = build_candidate_result(root, snapshot, _provenance(lengths=[1]), requested_count=1)
    ply = result.moves[0].continuation[0]
    bad_fen = ply.model_copy(update={"fen_before": "not-the-root"})
    bad_move = result.moves[0].model_copy(update={"continuation": [bad_fen]})
    with pytest.raises(CandidateResultValidationError):
        validate_candidate_result(result.model_copy(update={"moves": [bad_move]}), root)
    bad_delta = result.moves[0].model_copy(update={"material_delta": {"white": {"pawn": 1}, "black": {}}})
    with pytest.raises(CandidateResultValidationError):
        validate_candidate_result(result.model_copy(update={"moves": [bad_delta]}), root)


def test_validation_rejects_candidate_results_over_256_kib():
    root = chess.Board(ROOT_FEN)
    snapshot = CandidateSnapshot(depth=10, reports=[_report(1, "b1c3", 34)])
    result = build_candidate_result(root, snapshot, _provenance(lengths=[1]), requested_count=1)
    huge_description = "x" * (256 * 1024)
    oversized = result.model_copy(
        update={
            "moves": [
                result.moves[0].model_copy(update={"description": huge_description}),
            ]
        }
    )
    with pytest.raises(CandidateResultValidationError, match="256 KiB"):
        validate_candidate_result(oversized, root)


def test_build_rejects_insufficient_or_duplicate_root_candidates():
    root = chess.Board(ROOT_FEN)
    insufficient = CandidateSnapshot(depth=10, reports=[_report(1, "b1c3", 34)])
    with pytest.raises(CandidateResultValidationError):
        build_candidate_result(root, insufficient, _provenance(lengths=[1]), requested_count=2)
    duplicate = CandidateSnapshot(
        depth=10,
        reports=[_report(1, "b1c3", 34), _report(2, "b1c3", 10)],
    )
    with pytest.raises(CandidateResultValidationError):
        build_candidate_result(root, duplicate, _provenance(scores=[CpScore(kind="cp", value=34), CpScore(kind="cp", value=10)], lengths=[1, 1]), requested_count=2)
