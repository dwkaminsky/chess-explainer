"""Deterministic prose templates for factual chess explanations."""

from .grammar import (
    count_word,
    format_file_list,
    format_material_sentence,
    format_passed_sentence,
    format_square_list,
    format_square_noun_list,
    join_items,
)
from .render import render_factual_explanation

__all__ = [
    "count_word",
    "format_file_list",
    "format_material_sentence",
    "format_passed_sentence",
    "format_square_list",
    "format_square_noun_list",
    "join_items",
    "render_factual_explanation",
]

