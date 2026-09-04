import pytest

from app.fen import FenValidationError, normalize_fen

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_normalize_strips_whitespace_and_keeps_en_passant_field():
    fen = "rnbqkbnr/pppp1ppp/8/4p3/3PP3/8/PPP2PPP/RNBQKBNR w KQkq e6 0 2"
    assert normalize_fen(f"  {fen}\n") == fen


@pytest.mark.parametrize(
    "fen",
    [
        START.replace(" - 0 1", " - 0"),
        START.replace(" w ", " x "),
        START.replace(" KQkq ", " KK "),
        # White claims kingside castling without a rook on h1.
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBN1 w KQkq - 0 1",
        START.replace(" - 0 1", " z9 0 1"),
        # An en-passant target on rank three is only valid with Black to move.
        START.replace(" w KQkq -", " w KQkq e3"),
        START.replace(" - 0 1", " - -1 1"),
        START.replace(" - 0 1", " - 0 0"),
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBN9 w KQkq - 0 1",
    ],
)
def test_invalid_fen_is_rejected_before_storage(fen):
    with pytest.raises(FenValidationError):
        normalize_fen(fen)


def test_standard_board_validity_is_checked():
    no_black_king = "rnbq1bnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQ - 0 1"
    with pytest.raises(FenValidationError):
        normalize_fen(no_black_king)
