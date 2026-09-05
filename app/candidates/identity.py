"""Stable, branch-local piece identities used while replaying a PV."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import chess

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Mapping


_PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}
_PIECE_LETTERS = {
    chess.PAWN: "P",
    chess.KNIGHT: "N",
    chess.BISHOP: "B",
    chess.ROOK: "R",
    chess.QUEEN: "Q",
    chess.KING: "K",
}


class IdentityError(ValueError):
    """The identity ledger cannot be reconciled with a board transition."""


@dataclass
class LedgerPiece:
    id: str
    color: str
    type: str
    square: chess.Square
    active: bool = True

    def clone(self) -> "LedgerPiece":
        return LedgerPiece(self.id, self.color, self.type, self.square, self.active)


class IdentityLedger:
    """A mutable identity ledger; each candidate branch owns one instance."""

    def __init__(self, pieces: Mapping[str, LedgerPiece] | None = None) -> None:
        self.pieces: dict[str, LedgerPiece] = dict(pieces or {})

    def clone(self) -> "IdentityLedger":
        return IdentityLedger({piece_id: piece.clone() for piece_id, piece in self.pieces.items()})

    def active_at(self, square: chess.Square) -> LedgerPiece | None:
        for piece in self.pieces.values():
            if piece.active and piece.square == square:
                return piece
        return None

    def validate(self, board: chess.Board) -> None:
        seen: set[chess.Square] = set()
        active = [piece for piece in self.pieces.values() if piece.active]
        if len(active) != len(board.piece_map()):
            raise IdentityError("active ledger and board piece counts differ")
        for piece in active:
            if piece.square in seen:
                raise IdentityError("two active identities share a square")
            seen.add(piece.square)
            board_piece = board.piece_at(piece.square)
            if board_piece is None:
                raise IdentityError(f"active identity {piece.id} has no board piece")
            if ("white" if board_piece.color else "black") != piece.color:
                raise IdentityError(f"identity color mismatch for {piece.id}")
            if _PIECE_NAMES[board_piece.piece_type] != piece.type:
                raise IdentityError(f"identity type mismatch for {piece.id}")
        if seen != set(board.piece_map()):
            raise IdentityError("ledger is missing a board piece")


@dataclass
class MoveIdentity:
    mover: LedgerPiece
    mover_type_before: str
    mover_type_after: str
    from_square: chess.Square
    to_square: chess.Square
    captured: LedgerPiece | None = None
    rook: LedgerPiece | None = None
    rook_from: chess.Square | None = None
    rook_to: chess.Square | None = None
    promotion: str | None = None


def piece_type_name(piece_type: int) -> str:
    try:
        return _PIECE_NAMES[piece_type]
    except KeyError as exc:
        raise IdentityError(f"unknown chess piece type: {piece_type}") from exc


def initialize_ledger(board: chess.Board) -> IdentityLedger:
    pieces: dict[str, LedgerPiece] = {}
    for square, piece in sorted(board.piece_map().items()):
        color = "white" if piece.color == chess.WHITE else "black"
        piece_id = f"{color[0]}{_PIECE_LETTERS[piece.piece_type]}:{chess.square_name(square)}"
        pieces[piece_id] = LedgerPiece(
            id=piece_id,
            color=color,
            type=piece_type_name(piece.piece_type),
            square=square,
        )
    ledger = IdentityLedger(pieces)
    ledger.validate(board)
    return ledger


def _rook_squares(board: chess.Board, move: chess.Move) -> tuple[chess.Square, chess.Square]:
    rank = chess.square_rank(move.from_square)
    if chess.square_file(move.to_square) > chess.square_file(move.from_square):
        return chess.square(7, rank), chess.square(5, rank)
    return chess.square(0, rank), chess.square(3, rank)


def apply_move(board: chess.Board, move: chess.Move, ledger: IdentityLedger) -> MoveIdentity:
    """Apply one legal move to ``board`` and its ledger atomically."""

    if move not in board.legal_moves:
        raise IdentityError(f"illegal move: {move.uci()}")
    mover = ledger.active_at(move.from_square)
    board_mover = board.piece_at(move.from_square)
    if mover is None or board_mover is None:
        raise IdentityError("missing mover identity")
    mover_before = mover.type
    captured: LedgerPiece | None = None
    capture_square = move.to_square
    if board.is_en_passant(move):
        capture_square += -8 if board.turn == chess.WHITE else 8
    if board.piece_at(capture_square) is not None:
        captured = ledger.active_at(capture_square)
        if captured is None:
            raise IdentityError("missing captured identity")

    rook: LedgerPiece | None = None
    rook_from: chess.Square | None = None
    rook_to: chess.Square | None = None
    if board.is_castling(move):
        rook_from, rook_to = _rook_squares(board, move)
        rook = ledger.active_at(rook_from)
        if rook is None or board.piece_at(rook_from) is None:
            raise IdentityError("missing castling rook identity")

    promotion = piece_type_name(move.promotion) if move.promotion else None
    mover_after = promotion or mover_before
    identity = MoveIdentity(
        mover=mover,
        mover_type_before=mover_before,
        mover_type_after=mover_after,
        from_square=move.from_square,
        to_square=move.to_square,
        captured=captured,
        rook=rook,
        rook_from=rook_from,
        rook_to=rook_to,
        promotion=promotion,
    )

    mover.square = move.to_square
    mover.type = mover_after
    if captured is not None:
        captured.active = False
    if rook is not None and rook_to is not None:
        rook.square = rook_to
    board.push(move)
    ledger.validate(board)
    return identity


__all__ = [
    "IdentityError",
    "IdentityLedger",
    "LedgerPiece",
    "MoveIdentity",
    "apply_move",
    "initialize_ledger",
    "piece_type_name",
]
