"""Deterministic board-fact extraction for factual chess explanations."""

from .extract import (
    FACTUAL_RESULT_VERSION,
    ExplanationRenderError,
    FactExtractionError,
    FactualResultValidationError,
    build_factual_result,
    extract_facts,
    terminal_context_from_board,
)
from .models import (
    FILES,
    PIECE_ORDER,
    FileFacts,
    FactualResult,
    MaterialFacts,
    PawnFacts,
    PieceCounts,
    PieceDelta,
    PositionFacts,
    SemiOpenFiles,
    SidePawnFacts,
    TerminalState,
)

__all__ = [
    "FACTUAL_RESULT_VERSION",
    "FILES",
    "PIECE_ORDER",
    "ExplanationRenderError",
    "FactExtractionError",
    "FactualResult",
    "FactualResultValidationError",
    "FileFacts",
    "MaterialFacts",
    "PawnFacts",
    "PieceCounts",
    "PieceDelta",
    "PositionFacts",
    "SemiOpenFiles",
    "SidePawnFacts",
    "TerminalState",
    "build_factual_result",
    "extract_facts",
    "terminal_context_from_board",
]
