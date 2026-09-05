"""Deterministic board-fact extraction for factual chess explanations."""

from __future__ import annotations

from importlib import import_module
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

_EXTRACT_EXPORTS = {
    "FACTUAL_RESULT_VERSION",
    "ExplanationRenderError",
    "FactExtractionError",
    "FactualResultValidationError",
    "build_factual_result",
    "extract_facts",
    "terminal_context_from_board",
}


def __getattr__(name: str):
    if name in _EXTRACT_EXPORTS:
        module = import_module(".extract", __name__)
        return getattr(module, name)
    raise AttributeError(name)


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
