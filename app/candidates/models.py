"""Versioned, closed candidate-move records.

The models in this module describe the data contract only.  In particular,
they deliberately do not know about a chess board: legality, identity
ledgers, and factual extraction belong to the candidate replay modules.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

Square: TypeAlias = Annotated[StrictStr, Field(pattern=r"^[a-h][1-8]$")]
UCI: TypeAlias = Annotated[
    StrictStr,
    Field(pattern=r"^[a-h][1-8][a-h][1-8][nbrq]?$"),
]
Color: TypeAlias = Literal["white", "black"]
PieceType: TypeAlias = Literal["pawn", "knight", "bishop", "rook", "queen", "king"]
MaterialPieceType: TypeAlias = Literal["pawn", "knight", "bishop", "rook", "queen"]
FileName: TypeAlias = Literal["a", "b", "c", "d", "e", "f", "g", "h"]
JsonScalar: TypeAlias = StrictStr | StrictInt | StrictFloat | bool | None


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CpScore(_ClosedModel):
    kind: Literal["cp"]
    value: StrictInt


class MateScore(_ClosedModel):
    kind: Literal["mate"]
    winner: Color
    moves: StrictInt = Field(ge=0)


CandidateScore: TypeAlias = Annotated[CpScore | MateScore, Field(discriminator="kind")]


class CandidateReport(_ClosedModel):
    rank: StrictInt = Field(ge=1)
    depth: StrictInt = Field(ge=1)
    score: CandidateScore
    pv: list[UCI] = Field(min_length=1)
    seldepth: StrictInt | None = Field(default=None, ge=0)
    nodes: StrictInt | None = Field(default=None, ge=0)
    time_ms: StrictInt | None = Field(default=None, ge=0)


class CandidateSnapshot(_ClosedModel):
    depth: StrictInt = Field(ge=1)
    reports: list[CandidateReport] = Field(min_length=1, max_length=3)


class MoveRef(_ClosedModel):
    uci: UCI
    san: StrictStr = Field(min_length=1)


class MoverRecord(_ClosedModel):
    id: StrictStr = Field(min_length=1)
    color: Color
    type_before: PieceType
    type_after: PieceType
    from_square: Square = Field(
        validation_alias=AliasChoices("from", "from_square"),
        serialization_alias="from",
    )
    to_square: Square = Field(
        validation_alias=AliasChoices("to", "to_square"),
        serialization_alias="to",
    )


class CaptureRecord(_ClosedModel):
    id: StrictStr = Field(min_length=1)
    color: Color
    type: PieceType
    square: Square


class RookMoveRecord(_ClosedModel):
    id: StrictStr = Field(min_length=1)
    from_square: Square = Field(
        validation_alias=AliasChoices("from", "from_square"),
        serialization_alias="from",
    )
    to_square: Square = Field(
        validation_alias=AliasChoices("to", "to_square"),
        serialization_alias="to",
    )


class GroupMember(_ClosedModel):
    id: StrictStr = Field(min_length=1)
    square: Square


class MaterialDelta(_ClosedModel):
    """Sparse signed piece-count changes for each side."""

    white: dict[MaterialPieceType, StrictInt]
    black: dict[MaterialPieceType, StrictInt]


class MaterialChanged(_ClosedModel):
    id: StrictStr = Field(min_length=1)
    kind: Literal["material_changed"]
    delta: MaterialDelta


class PawnFeatureChanged(_ClosedModel):
    id: StrictStr = Field(min_length=1)
    kind: Literal["pawn_feature_changed"]
    pawn_id: StrictStr = Field(min_length=1)
    feature: Literal["isolated", "passed"]
    before: bool
    after: bool
    before_square: Square
    after_square: Square


class PawnGroupChanged(_ClosedModel):
    id: StrictStr = Field(min_length=1)
    kind: Literal["pawn_group_changed"]
    side: Color
    file: FileName
    before: list[GroupMember]
    after: list[GroupMember]


FileStatus: TypeAlias = Literal[
    "closed", "open", "semi_open_white", "semi_open_black"
]


class FileStatusChanged(_ClosedModel):
    id: StrictStr = Field(min_length=1)
    kind: Literal["file_status_changed"]
    file: FileName
    before: FileStatus
    after: FileStatus


Change: TypeAlias = Annotated[
    MaterialChanged | PawnFeatureChanged | PawnGroupChanged | FileStatusChanged,
    Field(discriminator="kind"),
]
PlyChange: TypeAlias = Change


class PlyRecord(_ClosedModel):
    ply: StrictInt = Field(ge=1)
    side: Color
    move_number: StrictInt = Field(ge=1)
    uci: UCI
    san: StrictStr = Field(min_length=1)
    fen_before: StrictStr = Field(min_length=1)
    fen_after: StrictStr = Field(min_length=1)
    mover: MoverRecord
    capture: CaptureRecord | None = None
    promotion: PieceType | None = None
    rook_move: RookMoveRecord | None = None
    changes: list[Change]


class DescriptionSource(_ClosedModel):
    sentence_index: StrictInt = Field(ge=0)
    plies: list[StrictInt] = Field(min_length=1)
    change_ids: list[StrictStr]


class MateDisplay(_ClosedModel):
    winner: Color
    moves: StrictInt = Field(ge=0)


class CandidateMove(_ClosedModel):
    rank: StrictInt = Field(ge=1)
    move: MoveRef
    evaluation: StrictFloat | None = None
    mate: MateDisplay | None = None
    gap_to_best: StrictFloat | None = Field(default=None, ge=0)
    continuation: list[PlyRecord] = Field(min_length=1, max_length=6)
    continuation_end: Literal["terminal", "prefix_limit", "engine_line_end"]
    material_delta: MaterialDelta
    description: StrictStr = Field(min_length=1)
    description_sources: list[DescriptionSource] = Field(max_length=3)

    @model_validator(mode="after")
    def _score_is_exactly_one_kind(self) -> "CandidateMove":
        if (self.evaluation is None) == (self.mate is None):
            raise ValueError("exactly one of evaluation or mate must be provided")
        if self.mate is not None and self.gap_to_best is not None:
            raise ValueError("gap_to_best must be null for a mate score")
        return self


class AnalysisMetadata(_ClosedModel):
    facts_version: Literal[1]
    root_side: Color
    requested_count: StrictInt = Field(ge=1, le=3)
    returned_count: StrictInt = Field(ge=0, le=3)
    snapshot_depth: StrictInt | None = Field(default=None, ge=1)
    search_budget_ms: StrictInt = Field(ge=1)
    max_continuation_plies: StrictInt = Field(ge=1, le=6)
    selection_policy: Literal["last_complete_depth", "terminal_position"]


class Provenance(_ClosedModel):
    normalized_fen: StrictStr = Field(min_length=1)
    engine_build: StrictStr = Field(min_length=1)
    network_hash: StrictStr | None = None
    options: dict[StrictStr, JsonScalar]
    selected_depth: StrictInt | None = Field(default=None, ge=1)
    raw_scores: list[CandidateScore]
    original_pv_lengths: list[StrictInt]
    elapsed_attempt_ms: StrictInt = Field(ge=0)
    elapsed_search_ms: StrictInt = Field(ge=0)
    elapsed_replay_ms: StrictInt = Field(ge=0)
    elapsed_render_ms: StrictInt = Field(ge=0)
    incomplete_groups: StrictInt = Field(ge=0)
    bound_only_groups: StrictInt = Field(ge=0)
    duplicate_root_groups: StrictInt = Field(ge=0)
    inconsistent_groups: StrictInt = Field(ge=0)

    @field_validator("original_pv_lengths")
    @classmethod
    def _pv_lengths_are_nonnegative(cls, value: list[int]) -> list[int]:
        if any(length < 0 for length in value):
            raise ValueError("original PV lengths must be nonnegative")
        return value


class CandidateResult(_ClosedModel):
    version: Literal[1]
    analysis: AnalysisMetadata
    moves: list[CandidateMove] = Field(max_length=3)
    provenance: Provenance

    @model_validator(mode="after")
    def _validate_bundle_invariants(self) -> "CandidateResult":
        moves_count = len(self.moves)
        if self.analysis.returned_count != moves_count:
            raise ValueError("analysis.returned_count must equal the number of moves")
        if len(self.provenance.raw_scores) != moves_count:
            raise ValueError("provenance.raw_scores must match the number of moves")
        if len(self.provenance.original_pv_lengths) != moves_count:
            raise ValueError("provenance.original_pv_lengths must match the number of moves")
        if self.provenance.selected_depth != self.analysis.snapshot_depth:
            raise ValueError("provenance.selected_depth must match analysis.snapshot_depth")

        is_terminal = moves_count == 0
        if is_terminal:
            if self.analysis.selection_policy != "terminal_position":
                raise ValueError("an empty candidate result requires terminal_position policy")
            if self.analysis.snapshot_depth is not None:
                raise ValueError("terminal candidate results have no snapshot depth")
        else:
            if self.analysis.selection_policy != "last_complete_depth":
                raise ValueError("non-empty candidate results require last_complete_depth policy")
            if self.analysis.snapshot_depth is None:
                raise ValueError("non-empty candidate results require a snapshot depth")
            expected_ranks = list(range(1, moves_count + 1))
            if [move.rank for move in self.moves] != expected_ranks:
                raise ValueError("candidate ranks must be contiguous and start at one")
        return self


__all__ = [
    "AnalysisMetadata",
    "CandidateMove",
    "CandidateReport",
    "CandidateResult",
    "CandidateScore",
    "CandidateSnapshot",
    "CaptureRecord",
    "Change",
    "CpScore",
    "DescriptionSource",
    "FileStatus",
    "FileStatusChanged",
    "GroupMember",
    "MaterialChanged",
    "MaterialDelta",
    "MaterialPieceType",
    "MateDisplay",
    "MateScore",
    "MoveRef",
    "MoverRecord",
    "PawnFeatureChanged",
    "PawnGroupChanged",
    "PieceType",
    "PlyChange",
    "PlyRecord",
    "Provenance",
    "RookMoveRecord",
    "Square",
    "UCI",
    "JsonScalar",
]
