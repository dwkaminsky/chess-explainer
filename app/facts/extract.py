"""Orchestration for deterministic factual chess explanations."""

from __future__ import annotations

import chess

from ..explanations.render import render_factual_explanation
from .files import extract_file_facts
from .material import extract_material
from .models import FactualResult, PositionFacts, TerminalState
from .pawns import extract_pawn_facts

FACTUAL_RESULT_VERSION = 1


class FactExtractionError(RuntimeError):
    code = "FACT_EXTRACTION_FAILED"


class ExplanationRenderError(RuntimeError):
    code = "EXPLANATION_RENDER_FAILED"


class FactualResultValidationError(RuntimeError):
    code = "FACTUAL_RESULT_INVALID"


def terminal_context_from_board(board: chess.Board) -> TerminalState:
    if board.is_checkmate():
        return TerminalState(kind="checkmate", winner="white" if board.turn == chess.BLACK else "black")
    if board.is_stalemate():
        return TerminalState(kind="stalemate")
    if board.is_insufficient_material():
        return TerminalState(kind="insufficient_material")
    if getattr(board, "is_seventyfive_moves", lambda: False)() or getattr(board, "is_variant_draw", lambda: False)():
        return TerminalState(kind="draw")
    return TerminalState(kind="none")


def extract_facts(board: chess.Board) -> PositionFacts:
    return PositionFacts(
        material=extract_material(board),
        pawns=extract_pawn_facts(board),
        files=extract_file_facts(board),
    )


def build_factual_result(board: chess.Board) -> FactualResult:
    try:
        facts = extract_facts(board)
    except Exception as exc:  # pragma: no cover - wrapped for worker policy
        raise FactExtractionError("board facts could not be extracted") from exc
    terminal_state = terminal_context_from_board(board)
    try:
        explanation = render_factual_explanation(facts, terminal_state)
    except Exception as exc:  # pragma: no cover - wrapped for worker policy
        raise ExplanationRenderError("factual explanation could not be generated") from exc
    try:
        return FactualResult.model_validate(
            {"version": FACTUAL_RESULT_VERSION, "facts": facts.model_dump(), "explanation": explanation}
        )
    except Exception as exc:  # pragma: no cover - wrapped for worker policy
        raise FactualResultValidationError("factual result did not validate") from exc
