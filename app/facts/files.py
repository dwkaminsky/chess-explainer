"""Open and semi-open file classification."""

from __future__ import annotations

import chess

from .models import FILES, FileFacts, SemiOpenFiles


def extract_file_facts(board: chess.Board) -> FileFacts:
    white_pawns = board.pieces(chess.PAWN, chess.WHITE)
    black_pawns = board.pieces(chess.PAWN, chess.BLACK)
    open_files: list[str] = []
    semi_white: list[str] = []
    semi_black: list[str] = []

    for file_index, file_name in enumerate(FILES):
        white_count = sum(1 for square in white_pawns if chess.square_file(square) == file_index)
        black_count = sum(1 for square in black_pawns if chess.square_file(square) == file_index)
        if white_count == 0 and black_count == 0:
            open_files.append(file_name)
        elif white_count == 0:
            semi_white.append(file_name)
        elif black_count == 0:
            semi_black.append(file_name)

    return FileFacts(open=open_files, semi_open=SemiOpenFiles(white=semi_white, black=semi_black))

