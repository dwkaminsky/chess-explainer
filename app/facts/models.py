"""Typed factual records persisted with completed chess tasks."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FILES = ("a", "b", "c", "d", "e", "f", "g", "h")
PIECE_ORDER = ("queen", "rook", "bishop", "knight", "pawn")
SQUARE_RE = r"^[a-h][1-8]$"
FILE_RE = r"^[a-h]$"
_FILE_INDEX = {file_name: index for index, file_name in enumerate(FILES)}


def _square_sort_key(square: str) -> tuple[int, int]:
    return _FILE_INDEX[square[0]], int(square[1])


def _validate_sorted_unique_squares(value: list[str]) -> list[str]:
    for item in value:
        if not isinstance(item, str) or re.fullmatch(SQUARE_RE, item) is None:
            raise ValueError("square names must be algebraic coordinates")
    if len(value) != len(set(value)):
        raise ValueError("square lists must not contain duplicates")
    if value != sorted(value, key=_square_sort_key):
        raise ValueError("square lists must be sorted by file then rank")
    return value


def _validate_sorted_unique_files(value: list[str]) -> list[str]:
    for file_name in value:
        if re.fullmatch(FILE_RE, file_name) is None:
            raise ValueError("file names must be in the range a-h")
    if len(value) != len(set(value)):
        raise ValueError("file lists must not contain duplicates")
    if value != sorted(value, key=FILES.index):
        raise ValueError("file lists must be sorted alphabetically")
    return value


class PieceCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queen: int = Field(ge=0)
    rook: int = Field(ge=0)
    bishop: int = Field(ge=0)
    knight: int = Field(ge=0)
    pawn: int = Field(ge=0)


class PieceDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queen: int
    rook: int
    bishop: int
    knight: int
    pawn: int


class MaterialFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    white: PieceCounts
    black: PieceCounts
    white_minus_black: PieceDelta

    @model_validator(mode="after")
    def _validate_deltas(self) -> "MaterialFacts":
        expected = {
            piece: getattr(self.white, piece) - getattr(self.black, piece)
            for piece in PIECE_ORDER
        }
        actual = {piece: getattr(self.white_minus_black, piece) for piece in PIECE_ORDER}
        if actual != expected:
            raise ValueError("white_minus_black must equal white counts minus black counts")
        return self


class SidePawnFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isolated: list[str] = Field(default_factory=list)
    doubled_files: dict[str, list[str]] = Field(default_factory=dict)
    passed: list[str] = Field(default_factory=list)

    @field_validator("isolated", "passed")
    @classmethod
    def _validate_square_list(cls, value: list[str]) -> list[str]:
        return _validate_sorted_unique_squares(value)

    @field_validator("doubled_files")
    @classmethod
    def _validate_doubled_files(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for file_name, squares in value.items():
            if file_name not in FILES:
                raise ValueError("pawn files must be in the range a-h")
            if len(squares) < 2:
                raise ValueError("doubled file groups must contain at least two pawns")
            _validate_sorted_unique_squares(squares)
            for square in squares:
                if square[0] != file_name:
                    raise ValueError("doubled file squares must match the mapped file")
        if list(value) != sorted(value, key=FILES.index):
            raise ValueError("doubled file groups must be sorted alphabetically")
        return value


class PawnFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    white: SidePawnFacts
    black: SidePawnFacts


class SemiOpenFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    white: list[str] = Field(default_factory=list)
    black: list[str] = Field(default_factory=list)

    @field_validator("white", "black")
    @classmethod
    def _validate_file_list(cls, value: list[str]) -> list[str]:
        return _validate_sorted_unique_files(value)

    @model_validator(mode="after")
    def _validate_disjoint(self) -> "SemiOpenFiles":
        if set(self.white) & set(self.black):
            raise ValueError("a file cannot be semi-open for both sides")
        return self


class FileFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open: list[str] = Field(default_factory=list)
    semi_open: SemiOpenFiles

    @field_validator("open")
    @classmethod
    def _validate_open_files(cls, value: list[str]) -> list[str]:
        return _validate_sorted_unique_files(value)

    @model_validator(mode="after")
    def _validate_no_overlap(self) -> "FileFacts":
        open_files = set(self.open)
        semi_open_files = set(self.semi_open.white) | set(self.semi_open.black)
        if open_files & semi_open_files:
            raise ValueError("open files cannot also be semi-open")
        return self


class PositionFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material: MaterialFacts
    pawns: PawnFacts
    files: FileFacts


class TerminalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["none", "checkmate", "stalemate", "insufficient_material", "draw"] = "none"
    winner: Literal["white", "black"] | None = None

    @model_validator(mode="after")
    def _validate_winner(self) -> "TerminalState":
        if self.kind == "checkmate" and self.winner not in {"white", "black"}:
            raise ValueError("checkmate requires an explicit winner")
        if self.kind != "checkmate" and self.winner is not None:
            raise ValueError("only checkmate can have a winner")
        return self


class FactualResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    facts: PositionFacts
    explanation: str = Field(min_length=1)
