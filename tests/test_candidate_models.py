from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.candidates import (
    AnalysisMetadata,
    CandidateMove,
    CandidateReport,
    CandidateResult,
    CandidateSnapshot,
    CaptureRecord,
    CpScore,
    DescriptionSource,
    FileStatusChanged,
    GroupMember,
    MaterialChanged,
    MaterialDelta,
    MateDisplay,
    MateScore,
    MoveRef,
    MoverRecord,
    PlyRecord,
    Provenance,
    PawnGroupChanged,
)


def _report(rank: int = 1) -> CandidateReport:
    return CandidateReport(
        rank=rank,
        depth=10,
        score=CpScore(kind="cp", value=34),
        pv=["e4d5", "d8d5"],
        seldepth=12,
        nodes=345,
        time_ms=89,
    )


def _snapshot() -> CandidateSnapshot:
    return CandidateSnapshot(depth=10, reports=[_report()])


def _move_ref() -> MoveRef:
    return MoveRef(uci="e4d5", san="exd5")


def _mover() -> MoverRecord:
    return MoverRecord(
        id="wP:e4",
        color="white",
        type_before="pawn",
        type_after="pawn",
        from_square="e4",
        to_square="d5",
    )


def _material_delta() -> MaterialDelta:
    return MaterialDelta(white={}, black={"pawn": -1})


def _ply() -> PlyRecord:
    return PlyRecord(
        ply=1,
        side="white",
        move_number=2,
        uci="e4d5",
        san="exd5",
        fen_before="root",
        fen_after="after",
        mover=_mover(),
        capture=CaptureRecord(id="bP:d5", color="black", type="pawn", square="d5"),
        changes=[
            MaterialChanged(id="e4d5:1:material", kind="material_changed", delta=_material_delta()),
            PawnGroupChanged(
                id="e4d5:1:group:white:d",
                kind="pawn_group_changed",
                side="white",
                file="d",
                before=[],
                after=[GroupMember(id="wP:d2", square="d2"), GroupMember(id="wP:e4", square="d5")],
            ),
            FileStatusChanged(
                id="e4d5:1:file:d",
                kind="file_status_changed",
                file="d",
                before="closed",
                after="semi_open_black",
            ),
        ],
    )


def _analysis(returned_count: int = 1, *, terminal: bool = False) -> AnalysisMetadata:
    return AnalysisMetadata(
        facts_version=1,
        root_side="white",
        requested_count=3,
        returned_count=returned_count,
        snapshot_depth=None if terminal else 10,
        search_budget_ms=3000,
        max_continuation_plies=6,
        selection_policy="terminal_position" if terminal else "last_complete_depth",
    )


def _provenance() -> Provenance:
    return Provenance(
        normalized_fen="root fen",
        engine_build="stockfish 15.1",
        network_hash=None,
        options={"Threads": 1, "Hash": 64, "MultiPV": 3},
        selected_depth=10,
        raw_scores=[CpScore(kind="cp", value=34)],
        original_pv_lengths=[2],
        elapsed_attempt_ms=100,
        elapsed_search_ms=80,
        elapsed_replay_ms=10,
        elapsed_render_ms=5,
        incomplete_groups=0,
        bound_only_groups=0,
        duplicate_root_groups=0,
        inconsistent_groups=0,
    )


def _terminal_provenance() -> Provenance:
    value = _provenance().model_dump()
    value.update(selected_depth=None, raw_scores=[], original_pv_lengths=[])
    return Provenance(**value)


def _candidate(rank: int = 1) -> CandidateMove:
    return CandidateMove(
        rank=rank,
        move=_move_ref(),
        evaluation=0.34,
        mate=None,
        gap_to_best=0.0,
        continuation=[_ply()],
        continuation_end="engine_line_end",
        material_delta=_material_delta(),
        description="exd5 captures a pawn.",
        description_sources=[DescriptionSource(sentence_index=0, plies=[1], change_ids=["e4d5:1:material"])],
    )


def test_schema_serializes_the_exact_public_shapes():
    result = CandidateResult(version=1, analysis=_analysis(), moves=[_candidate()], provenance=_provenance())
    dumped = result.model_dump(mode="json", by_alias=True)
    assert set(dumped) == {"version", "analysis", "moves", "provenance"}
    assert set(dumped["analysis"]) == {
        "facts_version", "root_side", "requested_count", "returned_count", "snapshot_depth",
        "search_budget_ms", "max_continuation_plies", "selection_policy",
    }
    assert dumped["moves"][0]["mate"] is None
    continuation = dumped["moves"][0]["continuation"][0]
    assert set(continuation["mover"]) == {"id", "color", "type_before", "type_after", "from", "to"}
    assert continuation["mover"]["from"] == "e4"
    assert continuation["changes"][0] == {
        "id": "e4d5:1:material", "kind": "material_changed", "delta": {"white": {}, "black": {"pawn": -1}
        },
    }
    assert set(dumped["provenance"]) == {
        "normalized_fen", "engine_build", "network_hash", "options", "selected_depth", "raw_scores",
        "original_pv_lengths", "elapsed_attempt_ms", "elapsed_search_ms", "elapsed_replay_ms",
        "elapsed_render_ms", "incomplete_groups", "bound_only_groups", "duplicate_root_groups",
        "inconsistent_groups",
    }


def test_score_variants_are_tagged_and_strict():
    assert CpScore(kind="cp", value=0).model_dump(mode="json") == {"kind": "cp", "value": 0}
    assert MateScore(kind="mate", winner="black", moves=3).model_dump(mode="json") == {
        "kind": "mate", "winner": "black", "moves": 3,
    }
    with pytest.raises(ValidationError):
        CpScore(kind="cp", value="34")
    with pytest.raises(ValidationError):
        MateScore(kind="mate", winner="white", moves=-1)


@pytest.mark.parametrize(
    "factory, payload",
    [
        (MoveRef, {"uci": "not-a-uci", "san": "e4"}),
        (CandidateReport, {"rank": 1, "depth": 10, "score": {"kind": "cp", "value": 1}, "pv": []}),
        (MoverRecord, {"id": "wP:e4", "color": "white", "type_before": "pawn", "type_after": "pawn", "from": "z9", "to": "e5"}),
        (FileStatusChanged, {"id": "x", "kind": "file_status_changed", "file": "a", "before": "closed", "after": "bad"}),
        (CandidateMove, {"rank": 1, "move": _move_ref(), "evaluation": None, "mate": None, "continuation": [], "continuation_end": "engine_line_end", "material_delta": _material_delta(), "description": "x", "description_sources": []}),
    ],
)
def test_invalid_shapes_are_rejected(factory, payload):
    with pytest.raises(ValidationError):
        factory(**payload)


def test_candidate_score_is_xor_and_gap_is_null_for_mate():
    with pytest.raises(ValidationError):
        CandidateMove(**{**_candidate().model_dump(), "evaluation": None, "mate": None})
    with pytest.raises(ValidationError):
        CandidateMove(**{**_candidate().model_dump(), "mate": MateDisplay(winner="black", moves=2)})


def test_result_invariants_enforce_counts_terminal_policy_and_ranks():
    with pytest.raises(ValidationError):
        CandidateResult(version=1, analysis=_analysis(returned_count=0), moves=[_candidate()], provenance=_provenance())
    with pytest.raises(ValidationError):
        CandidateResult(
            version=1,
            analysis=AnalysisMetadata(
                facts_version=1,
                root_side="white",
                requested_count=3,
                returned_count=0,
                snapshot_depth=None,
                search_budget_ms=3000,
                max_continuation_plies=6,
                selection_policy="last_complete_depth",
            ),
            moves=[],
            provenance=_terminal_provenance(),
        )
    with pytest.raises(ValidationError):
        CandidateResult(version=1, analysis=_analysis(returned_count=1), moves=[_candidate(rank=2)], provenance=_provenance())
    terminal = CandidateResult(version=1, analysis=_analysis(returned_count=0, terminal=True), moves=[], provenance=_terminal_provenance())
    assert terminal.model_dump(mode="json")["moves"] == []


def test_terminal_policy_and_provenance_are_consistent():
    with pytest.raises(ValidationError):
        CandidateResult(version=1, analysis=_analysis(), moves=[_candidate()], provenance=_terminal_provenance())
    with pytest.raises(ValidationError):
        CandidateResult(version=1, analysis=_analysis(), moves=[_candidate()], provenance=Provenance(**{
            **_provenance().model_dump(), "original_pv_lengths": [],
        }))


def test_structural_collection_bounds_are_enforced():
    with pytest.raises(ValidationError):
        CandidateSnapshot(depth=10, reports=[_report()] * 4)
    with pytest.raises(ValidationError):
        CandidateMove(**{**_candidate().model_dump(), "continuation": []})
    with pytest.raises(ValidationError):
        CandidateMove(**{**_candidate().model_dump(), "gap_to_best": -0.1})


def test_all_nested_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        MoveRef(uci="e2e4", san="e4", extra=1)
    with pytest.raises(ValidationError):
        MaterialChanged(id="m", kind="material_changed", delta=_material_delta(), extra=1)
    with pytest.raises(ValidationError):
        MaterialDelta(white={"king": 1}, black={})
