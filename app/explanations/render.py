"""Deterministic prose for the factual bundle."""

from __future__ import annotations

from ..facts.models import FileFacts, PawnFacts, PositionFacts, TerminalState
from .grammar import (
    count_word,
    format_file_open_clause,
    format_file_list,
    format_file_semi_open_clause,
    format_material_sentence,
    format_square_list,
    format_terminal_sentence,
    join_items,
)


def _word_count(text: str) -> int:
    return len(text.split())


def _trim_sentences(sentences: list[str], *, max_words: int = 120) -> list[str]:
    while sentences and _word_count(" ".join(sentences)) > max_words and len(sentences) > 1:
        sentences.pop()
    return sentences


def _doubled_clause(
    file_name: str,
    squares: list[str],
    isolated: set[str],
    passed: set[str],
) -> str:
    if len(squares) == 2:
        clause = f"doubled pawns on {format_square_list(squares)}"
    else:
        clause = f"{count_word(len(squares))} pawns on the {file_name}-file: {format_square_list(squares)}"
    overlap: list[str] = []
    if set(squares).issubset(isolated):
        overlap.append("isolated")
    if set(squares).issubset(passed):
        overlap.append("passed")
    if overlap:
        clause += f"; those pawns are {join_items(overlap)}"
    return clause


def _side_sentence(side: str, facts: PawnFacts) -> str | None:
    side_facts = getattr(facts, side.lower())
    isolated = list(side_facts.isolated)
    passed = list(side_facts.passed)
    doubled_files = side_facts.doubled_files

    if not isolated and not passed and not doubled_files:
        return None

    combined = [square for square in isolated if square in passed]
    doubled_squares = {
        square
        for squares in doubled_files.values()
        for square in squares
    }
    combined = [square for square in combined if square not in doubled_squares]
    isolated_only = [square for square in isolated if square not in combined and square not in doubled_squares]
    passed_only = [square for square in passed if square not in combined and square not in doubled_squares]

    if combined and not isolated_only and not passed_only and not doubled_files and len(combined) == 1:
        return f"{side}'s {combined[0]}-pawn is isolated and passed."

    clauses: list[str] = []
    if combined:
        if len(combined) == 1:
            clauses.append(f"an isolated and passed pawn on {combined[0]}")
        else:
            clauses.append(f"isolated and passed pawns on {format_square_list(combined)}")
    if isolated_only:
        if len(isolated_only) == 1:
            clauses.append(f"an isolated pawn on {isolated_only[0]}")
        else:
            clauses.append(f"isolated pawns on {format_square_list(isolated_only)}")
    if doubled_files:
        clauses.extend(
            _doubled_clause(file_name, squares, set(isolated), set(passed))
            for file_name, squares in doubled_files.items()
        )
    if passed_only:
        if len(passed_only) == 1:
            clauses.append(f"a passed pawn on {passed_only[0]}")
        else:
            clauses.append(f"passed pawns on {format_square_list(passed_only)}")

    if not clauses:
        return None
    return f"{side} has {'; '.join(clauses)}."


def _file_sentence(files: FileFacts) -> str | None:
    if not files.open and not files.semi_open.white and not files.semi_open.black:
        return None

    if len(files.open) == 8:
        open_clause = "Every file is open"
    elif files.open:
        open_clause = format_file_open_clause(files.open)
    else:
        open_clause = None

    semi_clauses: list[str] = []
    if files.semi_open.white:
        semi_clauses.append(format_file_semi_open_clause("White", files.semi_open.white))
    if files.semi_open.black:
        semi_clauses.append(format_file_semi_open_clause("Black", files.semi_open.black))

    if open_clause and semi_clauses:
        return f"{open_clause}; {'; '.join(semi_clauses)}."
    if open_clause:
        return f"{open_clause}."

    sentence = "; ".join(semi_clauses)
    return f"{sentence[0].upper()}{sentence[1:]}."


def render_factual_explanation(
    facts: PositionFacts,
    terminal_state: TerminalState | None = None,
) -> str:
    sentences: list[str] = []
    terminal_sentence = format_terminal_sentence(terminal_state) if terminal_state is not None else ""
    if terminal_sentence:
        sentences.append(terminal_sentence)
        material_sentence = format_material_sentence(facts.material)
        if material_sentence != "Both sides have the same material.":
            sentences.append(material_sentence)
        return " ".join(_trim_sentences(sentences))

    sentences.append(format_material_sentence(facts.material))

    if facts.material.white.pawn == 0 and facts.material.black.pawn == 0:
        sentences.append("Neither side has pawns.")
    else:
        white_sentence = _side_sentence("White", facts.pawns)
        black_sentence = _side_sentence("Black", facts.pawns)
        if white_sentence:
            sentences.append(white_sentence)
        if black_sentence:
            sentences.append(black_sentence)

    file_sentence = _file_sentence(facts.files)
    if file_sentence:
        sentences.append(file_sentence)

    return " ".join(_trim_sentences(sentences))
