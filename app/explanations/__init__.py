"""Deterministic prose templates for factual chess explanations."""

from .grammar import (
    count_word,
    format_doubled_sentence,
    format_file_list,
    format_material_sentence,
    format_passed_sentence,
    format_square_list,
    format_square_noun_list,
    format_terminal_sentence,
    join_items,
)
from .render import render_factual_explanation

__all__ = [
    "count_word",
    "format_doubled_sentence",
    "format_file_list",
    "format_material_sentence",
    "format_passed_sentence",
    "format_square_list",
    "format_square_noun_list",
    "format_terminal_sentence",
    "join_items",
    "render_factual_explanation",
]
