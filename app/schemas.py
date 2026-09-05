"""Pydantic models for the two public task endpoints."""

from __future__ import annotations

from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from .candidates import AnalysisMetadata, CandidateMove
from .facts.models import PositionFacts
from .fen import normalize_fen


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskCreate(BaseModel):
    """The deliberately closed submission contract."""

    model_config = ConfigDict(extra="forbid")

    fen: StrictStr

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, value: str) -> str:
        return normalize_fen(value)


class TaskError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: StrictStr
    message: StrictStr


class MateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner: Literal["white", "black"]
    moves: int = Field(ge=0)


TaskFacts = PositionFacts


class TaskCandidateAnalysis(AnalysisMetadata):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]


class TaskResult(BaseModel):
    """Response shape used by the API for queued, running, and terminal rows."""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    status: TaskStatus
    evaluation: float | None = None
    facts: TaskFacts | None = None
    explanation: StrictStr | None = None
    explanation_version: int | None = None
    candidate_moves: list[CandidateMove] | None = None
    candidate_analysis: TaskCandidateAnalysis | None = None
    mate: MateResult | None = None
    error: TaskError | None = None


# Friendly aliases for callers that prefer endpoint-oriented names.
SubmitTaskRequest = TaskCreate
FenRequest = TaskCreate
TaskResponse = TaskResult


__all__ = [
    "FenRequest",
    "MateResult",
    "SubmitTaskRequest",
    "TaskCandidateAnalysis",
    "TaskCreate",
    "TaskError",
    "TaskFacts",
    "TaskResponse",
    "TaskResult",
    "TaskStatus",
]
