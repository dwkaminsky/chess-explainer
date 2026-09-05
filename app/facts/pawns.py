"""Pawn-structure predicates for factual explanations."""

from __future__ import annotations

from collections import defaultdict

import chess

from .models import FILES, PawnFacts, SidePawnFacts


def _sorted_squares(squares: list[int]) -> list[str]:
    return [
        chess.square_name(square)
        for square in sorted(squares, key=lambda sq: (chess.square_file(sq), chess.square_rank(sq)))
    ]


def _side_facts(board: chess.Board, color: bool) -> SidePawnFacts:
    pawns = sorted(
        board.pieces(chess.PAWN, color),
        key=lambda sq: (chess.square_file(sq), chess.square_rank(sq)),
    )
    pawn_files = {chess.square_file(square) for square in pawns}
    enemy_pawns = list(board.pieces(chess.PAWN, not color))

    isolated: list[str] = []
    passed: list[str] = []
    grouped: dict[str, list[int]] = defaultdict(list)

    for square in pawns:
        file_index = chess.square_file(square)
        grouped[FILES[file_index]].append(square)
        if not any(adjacent in pawn_files for adjacent in (file_index - 1, file_index + 1) if 0 <= adjacent < 8):
            isolated.append(chess.square_name(square))

        rank_index = chess.square_rank(square)
        blocked = False
        for enemy_square in enemy_pawns:
            enemy_file = chess.square_file(enemy_square)
            if abs(enemy_file - file_index) > 1:
                continue
            enemy_rank = chess.square_rank(enemy_square)
            if color == chess.WHITE and enemy_rank > rank_index:
                blocked = True
                break
            if color == chess.BLACK and enemy_rank < rank_index:
                blocked = True
                break
        if not blocked:
            passed.append(chess.square_name(square))

    doubled_files = {
        file_name: _sorted_squares(squares)
        for file_name, squares in grouped.items()
        if len(squares) >= 2
    }

    return SidePawnFacts(
        isolated=_sorted_squares([chess.parse_square(square) for square in isolated]),
        doubled_files=doubled_files,
        passed=_sorted_squares([chess.parse_square(square) for square in passed]),
    )


def extract_pawn_facts(board: chess.Board) -> PawnFacts:
    return PawnFacts(
        white=_side_facts(board, chess.WHITE),
        black=_side_facts(board, chess.BLACK),
    )
