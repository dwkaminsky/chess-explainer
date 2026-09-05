"""Deterministic, qualified prose for a replayed candidate continuation."""

from __future__ import annotations

from dataclasses import dataclass

from ..candidates.models import (
    DescriptionSource,
    FileStatusChanged,
    MaterialChanged,
    PawnFeatureChanged,
    PlyRecord,
)
from ..facts.models import PositionFacts


class CandidateRenderError(ValueError):
    code = "CANDIDATE_RENDER_FAILED"


@dataclass(frozen=True)
class Sentence:
    text: str
    plies: tuple[int, ...]
    change_ids: tuple[str, ...]


@dataclass(frozen=True)
class DescriptionResult:
    description: str
    description_sources: list[DescriptionSource]


def _piece_label(color: str, piece_type: str, square: str) -> str:
    return f"{color.capitalize()}'s {square}-{piece_type}"


def _mechanical_sentence(ply: PlyRecord) -> str:
    return f"{ply.san} moves {ply.mover.color.capitalize()}'s {ply.mover.type_before} from {ply.mover.from_square} to {ply.mover.to_square}."


def _move_notation(ply: PlyRecord) -> str:
    return f"{ply.move_number}{'.' if ply.side == 'white' else '...'}{ply.san}"


def _root_sentence(ply: PlyRecord) -> Sentence:
    if ply.capture is not None:
        capture = _piece_label(ply.capture.color, ply.capture.type, ply.capture.square)
        material_ids = tuple(change.id for change in ply.changes if isinstance(change, MaterialChanged))
        file_changes = [change for change in ply.changes if isinstance(change, FileStatusChanged)]
        semi_white = next((change for change in file_changes if change.after == "semi_open_white"), None)
        if semi_white is not None:
            text = f"{ply.san} captures {capture} and makes the {semi_white.file}-file semi-open for White."
            return Sentence(text, (1,), material_ids + (semi_white.id,))
        text = f"{ply.san} captures {capture}."
        return Sentence(text, (1,), material_ids)
    if ply.promotion is not None:
        text = f"{ply.san} promotes {ply.mover.color.capitalize()}'s pawn on {ply.mover.to_square} to a {ply.promotion}."
        return Sentence(text, (1,), ())
    if ply.rook_move is not None:
        text = f"{ply.san} castles, moving {ply.mover.color.capitalize()}'s rook from {ply.rook_move.from_square} to {ply.rook_move.to_square}."
        return Sentence(text, (1,), ())
    for change in ply.changes:
        if isinstance(change, FileStatusChanged):
            if change.after == "open":
                text = f"{ply.san} opens the {change.file}-file."
            elif change.after.startswith("semi_open_"):
                side = change.after.removeprefix("semi_open_").capitalize()
                text = f"{ply.san} makes the {change.file}-file semi-open for {side}."
            else:
                continue
            return Sentence(text, (1,), (change.id,))
        if isinstance(change, PawnFeatureChanged) and change.after:
            text = f"{ply.san} leaves the {change.after_square}-pawn {change.feature}."
            return Sentence(text, (1,), (change.id,))
    return Sentence(_mechanical_sentence(ply), (1,), ())


def _follow_up_sentence(ply: PlyRecord, index: int, *, root_facts: PositionFacts, replay) -> Sentence | None:
    if ply.capture is None:
        return None
    capture = (
        f"{ply.capture.color.capitalize()}'s {ply.capture.type} on {ply.capture.square}"
    )
    suffix = ""
    snapshot = replay.snapshots[index]
    previous_snapshot = replay.snapshots[index - 1]
    root_material = root_facts.material
    current_material = snapshot.facts.material
    if (
        root_material.white.pawn == root_material.black.pawn
        and current_material.white.pawn == current_material.black.pawn
        and previous_snapshot.facts.material.white.pawn != previous_snapshot.facts.material.black.pawn
    ):
        suffix = ", restoring equal pawn counts"
    return Sentence(
        f"In the displayed continuation, {_move_notation(ply)} captures {capture}{suffix}.",
        (index,),
        tuple(change.id for change in ply.changes if isinstance(change, MaterialChanged)),
    )


def _preferred_follow_up(replay) -> tuple[int, PlyRecord] | None:
    """Return the most useful shown capture after the root move.

    If the root mover is captured later in the retained line, that recapture
    is more directly related to the root move than an unrelated intervening
    capture.  Keep the fallback to the first later capture deterministic.
    """

    root_mover_id = replay.continuation[0].mover.id
    first_capture: tuple[int, PlyRecord] | None = None
    for index, ply in enumerate(replay.continuation[1:], start=2):
        if ply.capture is None:
            continue
        if first_capture is None:
            first_capture = (index, ply)
        if ply.capture.id == root_mover_id:
            return index, ply
    return first_capture


def _material_qualification(replay, index: int, root_facts: PositionFacts) -> Sentence | None:
    delta = replay.material_delta
    if not delta.white and not delta.black:
        return None
    piece_names = ("queen", "rook", "bishop", "knight", "pawn")
    parts = []
    for side in ("white", "black"):
        before = getattr(root_facts.material, side)
        after = getattr(replay.endpoint_snapshot.facts.material, side)
        for piece in piece_names:
            change = getattr(after, piece) - getattr(before, piece)
            if change:
                parts.append(f"{side.capitalize()}'s {piece} count changes by {change:+d}")
    if not parts:
        return None
    text = f"At the end of the displayed line, {'; '.join(parts)}."
    return Sentence(text, (index,), ())


def describe_candidate(replay, root_facts: PositionFacts | None = None) -> DescriptionResult:
    """Render at most three sourced factual sentences for a replay result."""

    if not replay.continuation:
        raise CandidateRenderError("cannot describe an empty continuation")
    root_facts = root_facts or replay.root_snapshot.facts
    sentences: list[Sentence] = [_root_sentence(replay.continuation[0])]
    has_qualified_follow_up = False
    if replay.continuation[0].capture is not None:
        selected_follow_up = _preferred_follow_up(replay)
        if selected_follow_up is not None:
            index, ply = selected_follow_up
            follow_up = _follow_up_sentence(ply, index, root_facts=root_facts, replay=replay)
            if follow_up is not None:
                sentences.append(follow_up)
                has_qualified_follow_up = "restoring equal pawn counts" in follow_up.text
    if len(sentences) < 3 and not has_qualified_follow_up:
        qualification = _material_qualification(replay, len(replay.continuation), root_facts)
        if qualification is not None and qualification.text not in {sentence.text for sentence in sentences}:
            sentences.append(qualification)
    if len(sentences) > 3:
        sentences = sentences[:3]
    while len(" ".join(sentence.text for sentence in sentences).split()) > 90 and len(sentences) > 1:
        sentences.pop()
    description = " ".join(sentence.text for sentence in sentences)
    if len(description.split()) > 90:
        raise CandidateRenderError("candidate description exceeds 90 words")
    sources = [
        DescriptionSource(sentence_index=index, plies=list(sentence.plies), change_ids=list(sentence.change_ids))
        for index, sentence in enumerate(sentences)
    ]
    return DescriptionResult(description, sources)


__all__ = ["CandidateRenderError", "DescriptionResult", "Sentence", "describe_candidate"]
