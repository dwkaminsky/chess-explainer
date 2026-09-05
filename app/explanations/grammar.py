"""Grammar helpers for stable factual prose."""

from __future__ import annotations

from collections.abc import Sequence

from ..facts.models import PIECE_ORDER, MaterialFacts, TerminalState

COUNT_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}

PIECE_NAMES = {
    "queen": "queen",
    "rook": "rook",
    "bishop": "bishop",
    "knight": "knight",
    "pawn": "pawn",
}


def join_items(items: Sequence[str], *, conjunction: str = "and") -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conjunction} {items[1]}"
    return f"{', '.join(items[:-1])}, {conjunction} {items[-1]}"


def count_word(value: int) -> str:
    return COUNT_WORDS.get(value, str(value))


def _piece_name(piece: str, count: int) -> str:
    base = PIECE_NAMES[piece]
    return base if count == 1 else f"{base}s"


def format_square_list(squares: Sequence[str]) -> str:
    return join_items(list(squares))


def format_square_noun_list(squares: Sequence[str], noun: str) -> str:
    if len(squares) == 1:
        return f"{squares[0]}-{noun}"
    parts = [f"{square}-" for square in squares[:-1]] + [f"{squares[-1]}-{noun}s"]
    return join_items(parts)


def format_file_list(files: Sequence[str]) -> str:
    if len(files) == 1:
        return f"the {files[0]}-file"
    parts = [f"{file_name}-" for file_name in files[:-1]] + [f"{files[-1]}-files"]
    return f"the {join_items(parts)}"


def format_file_open_clause(files: Sequence[str]) -> str:
    if len(files) == 1:
        return f"The {files[0]}-file is open"
    parts = [f"{file_name}-" for file_name in files[:-1]] + [f"{files[-1]}-files"]
    return f"The {join_items(parts)} are open"


def format_file_semi_open_clause(side: str, files: Sequence[str]) -> str:
    if len(files) == 1:
        return f"the {files[0]}-file is semi-open for {side}"
    return f"{format_file_list(files)} are semi-open for {side}"


def format_file_semi_open_sentence(files: Sequence[str], side: str) -> str:
    if len(files) == 1:
        return f"The {files[0]}-file is semi-open for {side}."
    return f"{format_file_list(files)} are semi-open for {side}."


def format_material_sentence(material: MaterialFacts) -> str:
    positive: list[str] = []
    negative: list[str] = []
    for piece in PIECE_ORDER:
        diff = getattr(material.white_minus_black, piece)
        if diff > 0:
            positive.append(f"{count_word(diff)} more {_piece_name(piece, diff)}")
        elif diff < 0:
            amount = abs(diff)
            negative.append(f"{count_word(amount)} more {_piece_name(piece, amount)}")
    if not positive and not negative:
        return "Both sides have the same material."

    parts: list[str] = []
    if positive:
        parts.append(f"White has {join_items(positive)} than Black")
    if negative:
        parts.append(f"Black has {join_items(negative)} than White")
    return "; ".join(parts) + "."


def format_isolated_sentence(side: str, isolated: Sequence[str]) -> str:
    if len(isolated) == 1:
        return f"{side}'s {isolated[0]}-pawn is isolated."
    return f"{side}'s {format_square_noun_list(isolated, 'pawn')} are isolated."


def format_doubled_sentence(side: str, doubled_files: dict[str, list[str]]) -> str:
    if not doubled_files:
        return ""
    clauses: list[str] = []
    for file_name, squares in doubled_files.items():
        if len(squares) == 2:
            clauses.append(f"doubled pawns on the {file_name}-file")
        else:
            clauses.append(f"{count_word(len(squares))} pawns on the {file_name}-file")
    return f"{side} has {join_items(clauses)}."


def format_passed_sentence(side: str, passed: Sequence[str]) -> str:
    if not passed:
        return ""
    if len(passed) == 1:
        return f"{side} has a passed pawn on {passed[0]}."
    return f"{side} has passed pawns on {format_square_list(passed)}."


def format_terminal_sentence(state: TerminalState) -> str:
    if state.kind == "checkmate":
        return f"{'Black' if state.winner == 'white' else 'White'} is checkmated."
    if state.kind == "stalemate":
        return "The position is stalemate."
    if state.kind == "insufficient_material":
        return "The position is drawn by insufficient material."
    if state.kind == "draw":
        return "The position is drawn."
    return ""
