"""Deterministic prose for the factual bundle."""

from __future__ import annotations

from ..facts.models import FileFacts, PawnFacts, PositionFacts, TerminalState
from .grammar import (
    count_word,
    format_file_open_clause,
    format_file_list,
    format_isolated_sentence,
    format_material_sentence,
    format_passed_sentence,
    format_square_list,
    format_square_noun_list,
    format_terminal_sentence,
    join_items,
)


def _word_count(text: str) -> int:
    return len(text.split())


def _trim_sentences(sentences: list[str], *, max_words: int = 120) -> list[str]:
    while sentences and _word_count(" ".join(sentences)) > max_words and len(sentences) > 1:
        sentences.pop()
    return sentences


def _doubled_group_clause(
    file_name: str,
    squares: list[str],
) -> str:
    if len(squares) == 2:
        return f"doubled pawns on the {file_name}-file"
    return f"{count_word(len(squares))} pawns on the {file_name}-file"


def _side_sentences(side: str, facts: PawnFacts) -> list[str]:
    side_facts = getattr(facts, side.lower())
    isolated = list(side_facts.isolated)
    passed = list(side_facts.passed)
    doubled_files = side_facts.doubled_files

    combined = [square for square in isolated if square in passed]
    isolated_only = [square for square in isolated if square not in combined]
    passed_only = [square for square in passed if square not in combined]
    if combined and not isolated_only and not passed_only and not doubled_files:
        if len(combined) == 1:
            return [f"{side}'s {combined[0]}-pawn is isolated and passed."]
        return [f"{side}'s {format_square_noun_list(combined, 'pawn')} are isolated and passed."]

    sentences: list[str] = []
    if combined:
        if len(combined) == 1:
            sentences.append(f"{side}'s {combined[0]}-pawn is isolated and passed.")
        else:
            sentences.append(f"{side}'s {format_square_noun_list(combined, 'pawn')} are isolated and passed.")

    if len(isolated_only) == 1 and len(passed_only) == 1 and not doubled_files and not combined:
        return [format_isolated_sentence(side, isolated_only), format_passed_sentence(side, passed_only)]

    if isolated_only:
        sentences.append(format_isolated_sentence(side, isolated_only))

    if doubled_files:
        clauses = [_doubled_group_clause(file_name, squares) for file_name, squares in doubled_files.items()]
        if len(clauses) == 1:
            sentences.append(f"{side} has {clauses[0]}.")
        else:
            sentences.append(f"{side} has {join_items(clauses)}.")

    if passed_only:
        sentences.append(format_passed_sentence(side, passed_only))

    return sentences


def _file_sentences(files: FileFacts) -> list[str]:
    if len(files.open) == 8:
        open_clause = "Every file is open"
    elif files.open:
        open_clause = format_file_open_clause(files.open)
    else:
        open_clause = "No files are open"

    semi_clauses: list[str] = []
    if files.semi_open.white:
        semi_clauses.append(f"{format_file_list(files.semi_open.white)} are semi-open for White")
    if files.semi_open.black:
        semi_clauses.append(f"{format_file_list(files.semi_open.black)} are semi-open for Black")

    if semi_clauses:
        return [f"{open_clause}; {'; '.join(semi_clauses)}."]
    return [f"{open_clause}."]


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

    white_sentences = _side_sentences("White", facts.pawns)
    black_sentences = _side_sentences("Black", facts.pawns)
    if white_sentences:
        sentences.extend(white_sentences)
    if black_sentences:
        sentences.extend(black_sentences)
    if not white_sentences and not black_sentences:
        sentences.append("Neither side has isolated, doubled, or passed pawns.")
    sentences.extend(_file_sentences(facts.files))
    return " ".join(_trim_sentences(sentences))
