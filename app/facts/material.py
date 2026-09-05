"""Exact material inventory for a validated chess board."""

from __future__ import annotations

from typing import Any

import chess

from .models import MaterialFacts, PIECE_ORDER, PieceCounts, PieceDelta


def extract_material(board: chess.Board) -> MaterialFacts:
    def counts(color: bool) -> PieceCounts:
        return PieceCounts(
            queen=len(board.pieces(chess.QUEEN, color)),
            rook=len(board.pieces(chess.ROOK, color)),
            bishop=len(board.pieces(chess.BISHOP, color)),
            knight=len(board.pieces(chess.KNIGHT, color)),
            pawn=len(board.pieces(chess.PAWN, color)),
        )

    white = counts(chess.WHITE)
    black = counts(chess.BLACK)
    deltas = {
        piece: getattr(white, piece) - getattr(black, piece)
        for piece in PIECE_ORDER
    }
    return MaterialFacts(white=white, black=black, white_minus_black=PieceDelta(**deltas))
