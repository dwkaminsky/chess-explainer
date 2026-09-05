"""Typed factual records persisted with completed chess tasks."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FILES = ("a", "b", "c", "d", "e", "f", "g", "h")
PIECE_ORDER = ("queen", "rook", "bishop", "knight", "pawn")
SQUARE_RE = r"^[a-h][1-8]$"
FILE_RE = r"^[a-h]$"


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


class SidePawnFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isolated: list[str] = Field(default_factory=list)
    doubled_files: dict[str, list[str]] = Field(default_factory=dict)
    passed: list[str] = Field(default_factory=list)

    @field_validator("isolated", "passed")
    @classmethod
    def _validate_square_list(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or re.fullmatch(SQUARE_RE, item) is None:
                raise ValueError("square names must be algebraic coordinates")
        return value

    @field_validator("doubled_files")
    @classmethod
    def _validate_doubled_files(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for file_name, squares in value.items():
            if file_name not in FILES:
                raise ValueError("pawn files must be in the range a-h")
            for square in squares:
                if not isinstance(square, str) or re.fullmatch(SQUARE_RE, square) is None:
                    raise ValueError("square names must be algebraic coordinates")
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
        for file_name in value:
            if re.fullmatch(FILE_RE, file_name) is None:
                raise ValueError("file names must be in the range a-h")
        return value


class FileFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open: list[str] = Field(default_factory=list)
    semi_open: SemiOpenFiles

    @field_validator("open")
    @classmethod
    def _validate_open_files(cls, value: list[str]) -> list[str]:
        for file_name in value:
            if file_name not in FILES:
                raise ValueError("file names must be in the range a-h")
        return value


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
    explanation: str
