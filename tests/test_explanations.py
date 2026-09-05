from __future__ import annotations

from app.explanations.grammar import format_doubled_sentence
from app.explanations.render import render_factual_explanation
from app.facts.models import (
    FileFacts,
    MaterialFacts,
    PawnFacts,
    PieceCounts,
    PieceDelta,
    PositionFacts,
    SemiOpenFiles,
    SidePawnFacts,
    TerminalState,
)

SAMPLE_FACTS = PositionFacts(
    material=MaterialFacts(
        white=PieceCounts(queen=0, rook=0, bishop=0, knight=0, pawn=4),
        black=PieceCounts(queen=0, rook=0, bishop=0, knight=0, pawn=3),
        white_minus_black=PieceDelta(queen=0, rook=0, bishop=0, knight=0, pawn=1),
    ),
    pawns=PawnFacts(
        white=SidePawnFacts(isolated=["d4"], doubled_files={}, passed=["d4"]),
        black=SidePawnFacts(isolated=[], doubled_files={}, passed=[]),
    ),
    files=FileFacts(open=["a", "b", "c", "e"], semi_open=SemiOpenFiles(white=[], black=["d"])),
)

INITIAL_FACTS = PositionFacts(
    material=MaterialFacts(
        white=PieceCounts(queen=0, rook=0, bishop=0, knight=0, pawn=8),
        black=PieceCounts(queen=0, rook=0, bishop=0, knight=0, pawn=8),
        white_minus_black=PieceDelta(queen=0, rook=0, bishop=0, knight=0, pawn=0),
    ),
    pawns=PawnFacts(
        white=SidePawnFacts(),
        black=SidePawnFacts(),
    ),
    files=FileFacts(open=[], semi_open=SemiOpenFiles(white=[], black=[])),
)

PAWNLESS_FACTS = PositionFacts(
    material=MaterialFacts(
        white=PieceCounts(queen=0, rook=0, bishop=0, knight=0, pawn=0),
        black=PieceCounts(queen=0, rook=0, bishop=0, knight=0, pawn=0),
        white_minus_black=PieceDelta(queen=0, rook=0, bishop=0, knight=0, pawn=0),
    ),
    pawns=PawnFacts(
        white=SidePawnFacts(),
        black=SidePawnFacts(),
    ),
    files=FileFacts(
        open=list("abcdefgh"),
        semi_open=SemiOpenFiles(white=[], black=[]),
    ),
)

TERMINAL_FACTS = PositionFacts(
    material=MaterialFacts(
        white=PieceCounts(queen=1, rook=0, bishop=0, knight=0, pawn=0),
        black=PieceCounts(queen=0, rook=0, bishop=0, knight=0, pawn=0),
        white_minus_black=PieceDelta(queen=1, rook=0, bishop=0, knight=0, pawn=0),
    ),
    pawns=SAMPLE_FACTS.pawns,
    files=SAMPLE_FACTS.files,
)

EXPECTED_SAMPLE_EXPLANATION = (
    "White has one more pawn than Black. White's d4-pawn is isolated and passed. "
    "The a-, b-, c-, and e-files are open; the d-file is semi-open for Black."
)


def test_render_matches_the_worked_example_and_is_deterministic():
    first = render_factual_explanation(SAMPLE_FACTS)
    second = render_factual_explanation(SAMPLE_FACTS)

    assert first == second == EXPECTED_SAMPLE_EXPLANATION


def test_render_uses_the_initial_position_fallback_for_ordinary_pawn_structure():
    assert render_factual_explanation(INITIAL_FACTS) == (
        "Both sides have the same material. Neither side has isolated, doubled, or passed pawns."
    )


def test_render_formats_three_or_more_doubled_pawns_with_the_real_count():
    assert format_doubled_sentence("White", {"c": ["c2", "c4", "c6"]}) == "White has three pawns on the c-file: c2, c4, and c6."


def test_render_uses_terminal_sentence_and_suppresses_routine_pawn_and_file_prose():
    rendered = render_factual_explanation(
        TERMINAL_FACTS,
        TerminalState(kind="checkmate", winner="white"),
    )

    assert rendered == "Black is checkmated. White has one more queen than Black."
    assert "isolated" not in rendered
    assert "passed" not in rendered
    assert "file" not in rendered


def test_render_uses_the_pawnless_and_all_open_fallbacks():
    assert render_factual_explanation(PAWNLESS_FACTS) == (
        "Both sides have the same material. Neither side has pawns. Every file is open."
    )
