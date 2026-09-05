"""Identity-aware factual transitions for a replayed candidate line."""

from __future__ import annotations

from dataclasses import dataclass

import chess

from ..facts.extract import extract_facts
from ..facts.models import PIECE_ORDER, PositionFacts
from .identity import IdentityLedger
from .models import (
    Change,
    FileStatusChanged,
    GroupMember,
    MaterialChanged,
    MaterialDelta,
    PawnFeatureChanged,
    PawnGroupChanged,
)


class FactChangeError(ValueError):
    """A factual snapshot or transition could not be generated safely."""

    code = "FACT_CHANGE_FAILED"


@dataclass(frozen=True)
class FactSnapshot:
    facts: PositionFacts
    pawn_features: dict[str, tuple[bool, bool, str]]
    groups: dict[tuple[str, str], tuple[GroupMember, ...]]
    file_status: dict[str, str]


def _side_name(color: bool) -> str:
    return "white" if color == chess.WHITE else "black"


def _counts(facts: PositionFacts) -> dict[str, dict[str, int]]:
    return {
        "white": {piece: getattr(facts.material.white, piece) for piece in PIECE_ORDER},
        "black": {piece: getattr(facts.material.black, piece) for piece in PIECE_ORDER},
    }


def _sparse_delta(before: PositionFacts, after: PositionFacts) -> MaterialDelta:
    before_counts, after_counts = _counts(before), _counts(after)
    result: dict[str, dict[str, int]] = {"white": {}, "black": {}}
    for side in result:
        for piece in PIECE_ORDER:
            delta = after_counts[side][piece] - before_counts[side][piece]
            if delta:
                result[side][piece] = delta
    return MaterialDelta(**result)


def _pawn_maps(board: chess.Board, ledger: IdentityLedger) -> tuple[
    dict[str, tuple[bool, bool, str]], dict[tuple[str, str], tuple[GroupMember, ...]]
]:
    facts = extract_facts(board)
    square_to_id: dict[str, str] = {}
    for piece in ledger.pieces.values():
        if piece.active and piece.type == "pawn":
            square_to_id[chess.square_name(piece.square)] = piece.id

    features: dict[str, tuple[bool, bool, str]] = {}
    groups: dict[tuple[str, str], tuple[GroupMember, ...]] = {}
    for side, side_facts in (("white", facts.pawns.white), ("black", facts.pawns.black)):
        isolated = set(side_facts.isolated)
        passed = set(side_facts.passed)
        grouped: dict[str, list[GroupMember]] = {}
        for square, pawn_id in square_to_id.items():
            piece = ledger.pieces[pawn_id]
            if piece.color != side:
                continue
            features[pawn_id] = (square in isolated, square in passed, square)
            grouped.setdefault(square[0], []).append(GroupMember(id=pawn_id, square=square))
        for file_name, members in grouped.items():
            members.sort(key=lambda member: member.square)
            if len(members) >= 2:
                groups[(side, file_name)] = tuple(members)
    return features, groups


def _file_status(facts: PositionFacts) -> dict[str, str]:
    status = {file_name: "open" for file_name in "abcdefgh"}
    for file_name in facts.files.open:
        status[file_name] = "open"
    for file_name in facts.files.semi_open.white:
        status[file_name] = "semi_open_white"
    for file_name in facts.files.semi_open.black:
        status[file_name] = "semi_open_black"
    for file_name in "abcdefgh":
        if file_name not in facts.files.open and file_name not in facts.files.semi_open.white and file_name not in facts.files.semi_open.black:
            status[file_name] = "closed"
    return status


def snapshot_facts(board: chess.Board, ledger: IdentityLedger) -> FactSnapshot:
    try:
        facts = extract_facts(board)
        pawn_features, groups = _pawn_maps(board, ledger)
        return FactSnapshot(facts, pawn_features, groups, _file_status(facts))
    except FactChangeError:
        raise
    except Exception as exc:
        raise FactChangeError("factual snapshot generation failed") from exc


def diff_facts(before: FactSnapshot, after: FactSnapshot, root_uci: str, ply: int) -> list[Change]:
    """Return deterministic semantic changes between consecutive snapshots."""
    try:
        changes: list[Change] = []
        material = _sparse_delta(before.facts, after.facts)
        if material.white or material.black:
            changes.append(
                MaterialChanged(id=f"{root_uci}:{ply}:material", kind="material_changed", delta=material)
            )
        for pawn_id in sorted(set(before.pawn_features) & set(after.pawn_features)):
            before_isolated, before_passed, before_square = before.pawn_features[pawn_id]
            after_isolated, after_passed, after_square = after.pawn_features[pawn_id]
            for feature, old, new in (
                ("isolated", before_isolated, after_isolated),
                ("passed", before_passed, after_passed),
            ):
                if old != new:
                    changes.append(
                        PawnFeatureChanged(
                            id=f"{root_uci}:{ply}:pawn_feature:{pawn_id}:{feature}",
                            kind="pawn_feature_changed",
                            pawn_id=pawn_id,
                            feature=feature,
                            before=old,
                            after=new,
                            before_square=before_square,
                            after_square=after_square,
                        )
                    )

        for side, file_name in sorted(set(before.groups) | set(after.groups)):
            old_group = before.groups.get((side, file_name), ())
            new_group = after.groups.get((side, file_name), ())
            if {member.id for member in old_group} != {member.id for member in new_group}:
                changes.append(
                    PawnGroupChanged(
                        id=f"{root_uci}:{ply}:group:{side}:{file_name}",
                        kind="pawn_group_changed",
                        side=side,
                        file=file_name,
                        before=list(old_group),
                        after=list(new_group),
                    )
                )

        for file_name in "abcdefgh":
            old_status, new_status = before.file_status[file_name], after.file_status[file_name]
            if old_status != new_status:
                changes.append(
                    FileStatusChanged(
                        id=f"{root_uci}:{ply}:file:{file_name}",
                        kind="file_status_changed",
                        file=file_name,
                        before=old_status,
                        after=new_status,
                    )
                )
        return changes
    except FactChangeError:
        raise
    except Exception as exc:
        raise FactChangeError("factual transition generation failed") from exc


def material_delta_between(before: FactSnapshot, after: FactSnapshot) -> MaterialDelta:
    try:
        return _sparse_delta(before.facts, after.facts)
    except FactChangeError:
        raise
    except Exception as exc:
        raise FactChangeError("material transition generation failed") from exc


def sum_material_deltas(changes_by_ply: list[list[Change]]) -> MaterialDelta:
    total: dict[str, dict[str, int]] = {"white": {}, "black": {}}
    for changes in changes_by_ply:
        for change in changes:
            if not isinstance(change, MaterialChanged):
                continue
            for side in total:
                for piece, delta in getattr(change.delta, side).items():
                    total[side][piece] = total[side].get(piece, 0) + delta
                    if total[side][piece] == 0:
                        del total[side][piece]
    return MaterialDelta(**total)


def assert_material_deltas(changes_by_ply: list[list[Change]], expected: MaterialDelta) -> None:
    try:
        actual = sum_material_deltas(changes_by_ply)
        if actual != expected:
            raise FactChangeError("per-ply material changes do not equal endpoint material delta")
    except FactChangeError:
        raise
    except Exception as exc:
        raise FactChangeError("material transition validation failed") from exc


__all__ = [
    "FactChangeError",
    "FactSnapshot",
    "assert_material_deltas",
    "diff_facts",
    "material_delta_between",
    "snapshot_facts",
    "sum_material_deltas",
]
