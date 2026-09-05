from __future__ import annotations

import chess
import pytest

import app.candidates.replay as replay_module
from app.candidates import (
    CandidateReport,
    CpScore,
    InvalidEnginePV,
    ReplayStateMismatch,
    initialize_ledger,
    replay_candidate,
)


ROOT_FEN = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2"


def report(*moves: str) -> CandidateReport:
    return CandidateReport(rank=1, depth=10, score=CpScore(kind="cp", value=34), pv=list(moves))


def test_capture_recapture_fixture_records_identity_and_sparse_material():
    result = replay_candidate(
        chess.Board(ROOT_FEN),
        report("e4d5", "d8d5", "b1c3", "d5d8", "d2d4", "g8f6"),
        max_plies=6,
    )
    assert result.continuation_end == "engine_line_end"
    first = result.continuation[0]
    assert first.fen_before == ROOT_FEN
    assert chess.Board(ROOT_FEN).fen(en_passant="fen") == first.fen_before
    assert first.side == "white"
    assert first.move_number == 2
    assert first.uci == "e4d5"
    assert first.san == "exd5"
    assert first.mover.id == "wP:e4"
    assert first.mover.from_square == "e4"
    assert first.mover.to_square == "d5"
    assert first.capture is not None
    assert first.capture.id == "bP:d5"
    assert first.capture.square == "d5"
    assert first.changes[0].id == "e4d5:1:material"
    assert first.changes[0].delta.white == {}
    assert first.changes[0].delta.black == {"pawn": -1}
    assert result.continuation[1].capture is not None
    assert result.continuation[1].capture.id == "wP:e4"
    assert result.material_delta.white == {"pawn": -1}
    assert result.material_delta.black == {"pawn": -1}
    assert result.ledger.active_at(chess.parse_square("d4")).id == "wP:d2"


def test_prefix_limit_does_not_validate_or_describe_tail():
    result = replay_candidate(
        chess.Board(ROOT_FEN),
        report("e4d5", "d8d5", "b1c3", "d5d8", "d2d4", "g8f6", "a1a2"),
        max_plies=6,
    )
    assert len(result.continuation) == 6
    assert result.continuation_end == "prefix_limit"


def test_en_passant_uses_actual_capture_square():
    board = chess.Board("8/8/8/3pP3/8/8/8/4K2k w - d6 0 1")
    result = replay_candidate(board, report("e5d6"), max_plies=6)
    ply = result.continuation[0]
    assert ply.mover.id == "wP:e5"
    assert ply.capture is not None
    assert ply.capture.id == "bP:d5"
    assert ply.capture.square == "d5"
    assert result.material_delta.black == {"pawn": -1}


@pytest.mark.parametrize(
    "fen,uci,king_from,king_to,rook_id,rook_from,rook_to",
    [
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1", "e1", "g1", "wR:h1", "h1", "f1"),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "e8c8", "e8", "c8", "bR:a8", "a8", "d8"),
    ],
)
def test_castling_moves_both_piece_identities(fen, uci, king_from, king_to, rook_id, rook_from, rook_to):
    ply = replay_candidate(chess.Board(fen), report(uci)).continuation[0]
    assert ply.mover.id == ("wK:e1" if king_from == "e1" else "bK:e8")
    assert ply.mover.from_square == king_from
    assert ply.mover.to_square == king_to
    assert ply.rook_move is not None
    assert ply.rook_move.id == rook_id
    assert ply.rook_move.from_square == rook_from
    assert ply.rook_move.to_square == rook_to
    assert ply.capture is None


@pytest.mark.parametrize(
    "fen,uci,new_type",
    [
        ("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a7a8q", "queen"),
        ("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a7a8n", "knight"),
        ("r3k3/1P6/8/8/8/8/8/4K3 w - - 0 1", "b7a8q", "queen"),
    ],
)
def test_promotion_retains_pawn_identity_and_changes_current_type(fen, uci, new_type):
    ply = replay_candidate(chess.Board(fen), report(uci)).continuation[0]
    assert ply.mover.id in {"wP:a7", "wP:b7"}
    assert ply.mover.type_before == "pawn"
    assert ply.mover.type_after == new_type
    assert ply.promotion == new_type


def test_candidate_branches_get_independent_ledgers():
    root = chess.Board(ROOT_FEN)
    first = replay_candidate(root, report("e4d5"))
    second = replay_candidate(root, report("e4e5", "d5d4"))
    assert first.continuation[0].mover.id == "wP:e4"
    assert second.continuation[0].mover.id == "wP:e4"
    assert root.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert root.fen(en_passant="fen") == ROOT_FEN
    assert first.ledger.active_at(chess.D5).id == "wP:e4"
    assert second.ledger.active_at(chess.E5).id == "wP:e4"


def test_illegal_and_null_pv_are_rejected():
    empty = CandidateReport.model_construct(rank=1, depth=10, score=CpScore(kind="cp", value=34), pv=[])
    malformed = CandidateReport.model_construct(rank=1, depth=10, score=CpScore(kind="cp", value=34), pv=[None])
    with pytest.raises(InvalidEnginePV) as error:
        replay_candidate(chess.Board(ROOT_FEN), empty)
    assert error.value.code == "INVALID_ENGINE_PV"
    with pytest.raises(InvalidEnginePV):
        replay_candidate(chess.Board(ROOT_FEN), report("e2e5"))
    with pytest.raises(InvalidEnginePV):
        replay_candidate(chess.Board(ROOT_FEN), malformed)


def test_root_ids_are_deterministic_and_use_knight_letter_n():
    ledger = initialize_ledger(chess.Board())
    assert "wN:b1" in ledger.pieces
    assert "wK:e1" in ledger.pieces
    assert "bQ:d8" in ledger.pieces


def test_advancing_passer_has_no_false_feature_creation():
    board = chess.Board("4k3/8/8/3P4/8/8/8/4K3 w - - 0 1")
    result = replay_candidate(board, report("d5d6"))
    assert not any(change.kind == "pawn_feature_changed" for change in result.continuation[0].changes)


def test_doubled_group_persists_when_a_member_advances_and_ends_on_capture():
    persistent = chess.Board("4k3/8/8/8/3P4/8/3P4/4K3 w - - 0 1")
    result = replay_candidate(persistent, report("d4d5"))
    assert not any(change.kind == "pawn_group_changed" for change in result.continuation[0].changes)

    ending = chess.Board("4k3/8/8/4p3/3P4/8/3P4/4K3 w - - 0 1")
    result = replay_candidate(ending, report("d4e5"))
    groups = [change for change in result.continuation[0].changes if change.kind == "pawn_group_changed"]
    assert len(groups) == 1
    assert groups[0].before[0].id == "wP:d2"
    assert groups[0].after == []


def test_identity_invariant_is_a_non_retryable_state_mismatch(monkeypatch):
    def broken_apply_move(*args, **kwargs):
        from app.candidates.identity import IdentityError

        raise IdentityError("corrupt ledger")

    monkeypatch.setattr(replay_module, "apply_move", broken_apply_move)
    with pytest.raises(ReplayStateMismatch) as error:
        replay_candidate(chess.Board(ROOT_FEN), report("e4d5"))
    assert error.value.code == "REPLAY_STATE_MISMATCH"
