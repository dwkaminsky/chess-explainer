"""Validation and normalization for standard-chess Forsyth-Edwards Notation.

The API stores the normalized value returned here.  Validation deliberately
comes before calling :meth:`chess.Board.fen`: python-chess can normalize some
state while parsing, and doing so before validation would hide malformed
castling or clock fields.
"""

from __future__ import annotations

import re
from typing import Final

import chess

MAX_FEN_LENGTH: Final[int] = 256
_PIECE_CHARS: Final[str] = "prnbqkPRNBQK"
_CASTLING_RE: Final[re.Pattern[str]] = re.compile(r"^[KQkq]+$")
_SQUARE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-h][36]$")
_UNSIGNED_INTEGER_RE: Final[re.Pattern[str]] = re.compile(r"^(?:0|[1-9][0-9]*)$")


class FenValidationError(ValueError):
    """Raised when a supplied FEN is not a valid standard-chess position."""


def _validate_piece_placement(placement: str) -> None:
    ranks = placement.split("/")
    if len(ranks) != 8:
        raise FenValidationError("FEN board must contain eight ranks")

    for rank in ranks:
        width = 0
        for char in rank:
            if char in _PIECE_CHARS:
                width += 1
            elif char in "12345678":
                width += int(char)
            else:
                raise FenValidationError("FEN board contains an invalid piece or digit")
        if width != 8:
            raise FenValidationError("each FEN rank must contain eight squares")


def _validate_fields(fields: list[str]) -> None:
    if len(fields) != 6:
        raise FenValidationError("FEN must contain exactly six fields")

    _validate_piece_placement(fields[0])

    if fields[1] not in {"w", "b"}:
        raise FenValidationError("FEN active color must be 'w' or 'b'")

    castling = fields[2]
    if castling != "-":
        if not _CASTLING_RE.fullmatch(castling) or len(set(castling)) != len(castling):
            raise FenValidationError("FEN contains invalid castling rights")

    en_passant = fields[3]
    if en_passant != "-" and not _SQUARE_RE.fullmatch(en_passant):
        raise FenValidationError("FEN contains an invalid en-passant square")
    if en_passant != "-":
        expected_rank = "6" if fields[1] == "w" else "3"
        if en_passant[1] != expected_rank:
            raise FenValidationError(
                "en-passant square rank does not match the side to move"
            )

    if not _UNSIGNED_INTEGER_RE.fullmatch(fields[4]):
        raise FenValidationError("FEN halfmove clock must be a nonnegative integer")
    if not _UNSIGNED_INTEGER_RE.fullmatch(fields[5]) or int(fields[5]) < 1:
        raise FenValidationError("FEN fullmove number must be a positive integer")


def normalize_fen(value: str) -> str:
    """Validate *value* and return a canonical, six-field FEN.

    Surrounding whitespace and arbitrary internal runs of whitespace are
    normalized.  The en-passant field is emitted in ``fen`` mode, rather than
    python-chess's default ``legal`` mode, so a valid FEN state is not silently
    dropped merely because no legal capture is currently available.
    """

    if not isinstance(value, str):
        raise FenValidationError("FEN must be a string")

    stripped = value.strip()
    if len(stripped) > MAX_FEN_LENGTH:
        raise FenValidationError("FEN exceeds the maximum length")

    fields = stripped.split()
    _validate_fields(fields)

    try:
        board = chess.Board(stripped, chess960=False)
    except Exception as exc:  # python-chess uses ValueError across versions.
        raise FenValidationError("FEN could not be parsed") from exc

    if not board.is_valid():
        raise FenValidationError("FEN does not describe a valid standard-chess board")

    try:
        normalized = board.fen(en_passant="fen")
    except TypeError:  # Compatibility with older python-chess releases.
        normalized = board.fen()
        normalized_fields = normalized.split()
        normalized_fields[3] = fields[3]
        normalized = " ".join(normalized_fields)

    # Keep this invariant even if a future python-chess release changes its
    # interpretation of the output mode.
    normalized_fields = normalized.split()
    if len(normalized_fields) != 6:
        raise FenValidationError("python-chess returned an invalid normalized FEN")
    normalized_fields[3] = fields[3]
    return " ".join(normalized_fields)


# ``validate_fen`` is a convenient descriptive alias for callers that only
# need the validation side effect but still want the normalized return value.
validate_fen = normalize_fen


__all__ = ["FenValidationError", "MAX_FEN_LENGTH", "normalize_fen", "validate_fen"]
