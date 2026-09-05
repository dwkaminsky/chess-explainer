"""Legal, bounded replay of one candidate principal variation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import chess

from ..facts.extract import terminal_context_from_board
from .changes import (
    FactChangeError,
    FactSnapshot,
    assert_material_deltas,
    diff_facts,
    material_delta_between,
    snapshot_facts,
)
from .identity import IdentityError, IdentityLedger, apply_move, initialize_ledger
from .models import CaptureRecord, CandidateReport, MaterialDelta, MoveRef, MoverRecord, PlyRecord, RookMoveRecord


class ReplayError(ValueError):
    """A candidate PV cannot be retained as a legal replay."""


class InvalidEnginePV(ReplayError):
    code = "INVALID_ENGINE_PV"


class ReplayStateMismatch(ReplayError):
    code = "REPLAY_STATE_MISMATCH"


@dataclass
class ReplayResult:
    continuation: list[PlyRecord]
    continuation_end: Literal["terminal", "prefix_limit", "engine_line_end"]
    material_delta: MaterialDelta
    root_snapshot: FactSnapshot
    endpoint_snapshot: FactSnapshot
    ledger: IdentityLedger
    snapshots: list[FactSnapshot]


def _move_from_uci(raw: object) -> chess.Move:
    if isinstance(raw, chess.Move):
        return raw
    if not isinstance(raw, str):
        raise InvalidEnginePV("PV contains a non-UCI move")
    try:
        return chess.Move.from_uci(raw)
    except ValueError as exc:
        raise InvalidEnginePV(f"PV contains malformed UCI: {raw!r}") from exc


def _expected_root_move(report: object) -> str | None:
    candidate_move = getattr(report, "move", None)
    if candidate_move is None:
        return None
    if isinstance(candidate_move, MoveRef):
        return candidate_move.uci
    value = getattr(candidate_move, "uci", candidate_move)
    return value if isinstance(value, str) else None


def replay_candidate(
    root: chess.Board,
    report: CandidateReport,
    max_plies: int = 6,
    *,
    expected_move: str | MoveRef | None = None,
) -> ReplayResult:
    """Replay a report's legal PV from an untouched copy of ``root``.

    Only retained plies are parsed and validated.  A raw PV tail beyond the
    display cap is intentionally not interpreted; it only determines the
    ``prefix_limit`` ending reason.
    """

    if max_plies < 1 or max_plies > 6:
        raise InvalidEnginePV("max_plies must be between one and six")
    raw_pv = list(getattr(report, "pv", ()))
    if not raw_pv:
        raise InvalidEnginePV("candidate PV must be nonempty")

    root_move = _move_from_uci(raw_pv[0])
    expected = expected_move.uci if isinstance(expected_move, MoveRef) else expected_move
    expected = expected or _expected_root_move(report)
    if expected is not None:
        try:
            if root_move.uci() != _move_from_uci(expected).uci():
                raise InvalidEnginePV("candidate PV does not begin with the requested root move")
        except ReplayError:
            raise
    if root_move not in root.legal_moves:
        raise InvalidEnginePV(f"candidate root move is illegal: {root_move.uci()}")

    board = root.copy(stack=True)
    ledger = initialize_ledger(board)
    root_snapshot = snapshot_facts(board, ledger)
    records: list[PlyRecord] = []
    snapshots = [root_snapshot]
    changes_by_ply = []

    for raw_move in raw_pv[:max_plies]:
        move = _move_from_uci(raw_move)
        if move not in board.legal_moves:
            raise InvalidEnginePV(f"candidate PV contains illegal move: {move.uci()}")
        side = "white" if board.turn == chess.WHITE else "black"
        move_number = board.fullmove_number
        fen_before = board.fen(en_passant="fen")
        san = board.san(move)
        before = snapshots[-1]
        try:
            identity = apply_move(board, move, ledger)
        except IdentityError as exc:
            raise ReplayStateMismatch(str(exc)) from exc
        after = snapshot_facts(board, ledger)
        snapshots.append(after)
        changes = diff_facts(before, after, root_move.uci(), len(records) + 1)
        changes_by_ply.append(changes)

        capture = None
        if identity.captured is not None:
            capture = CaptureRecord(
                id=identity.captured.id,
                color=identity.captured.color,
                type=identity.captured.type,
                square=chess.square_name(identity.captured.square),
            )
        rook_move = None
        if identity.rook is not None and identity.rook_from is not None and identity.rook_to is not None:
            rook_move = RookMoveRecord(
                id=identity.rook.id,
                from_square=chess.square_name(identity.rook_from),
                to_square=chess.square_name(identity.rook_to),
            )
        records.append(
            PlyRecord(
                ply=len(records) + 1,
                side=side,
                move_number=move_number,
                uci=move.uci(),
                san=san,
                fen_before=fen_before,
                fen_after=board.fen(en_passant="fen"),
                mover=MoverRecord(
                    id=identity.mover.id,
                    color=identity.mover.color,
                    type_before=identity.mover_type_before,
                    type_after=identity.mover_type_after,
                    from_square=chess.square_name(identity.from_square),
                    to_square=chess.square_name(identity.to_square),
                ),
                capture=capture,
                promotion=identity.promotion,
                rook_move=rook_move,
                changes=changes,
            )
        )
        if terminal_context_from_board(board).kind != "none":
            ending = "terminal"
            break
    else:
        ending = "prefix_limit" if len(raw_pv) > max_plies else "engine_line_end"

    endpoint_snapshot = snapshots[-1]
    endpoint_delta = material_delta_between(root_snapshot, endpoint_snapshot)
    try:
        assert_material_deltas(changes_by_ply, endpoint_delta)
    except FactChangeError:
        raise
    except ValueError as exc:
        raise ReplayStateMismatch(str(exc)) from exc
    return ReplayResult(records, ending, endpoint_delta, root_snapshot, endpoint_snapshot, ledger, snapshots)


__all__ = [
    "InvalidEnginePV",
    "ReplayError",
    "ReplayResult",
    "ReplayStateMismatch",
    "replay_candidate",
]
