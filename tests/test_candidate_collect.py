from __future__ import annotations

import chess
import pytest

from app.candidates.models import CandidateReport, CandidateSnapshot, CpScore, MateScore
from app.candidates.collect import (
    IncompleteCandidateSet,
    collect_reports,
)


def _report(
    rank: int,
    depth: int,
    score: object,
    pv: list[str],
    *,
    multipv: int | None = None,
    seldepth: int | None = None,
    nodes: int | None = None,
    time_ms: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"rank": rank, "depth": depth, "score": score, "pv": pv}
    if multipv is not None:
        payload["multipv"] = multipv
    if seldepth is not None:
        payload["seldepth"] = seldepth
    if nodes is not None:
        payload["nodes"] = nodes
    if time_ms is not None:
        payload["time_ms"] = time_ms
    return payload


def _board() -> chess.Board:
    return chess.Board()


def test_collect_returns_latest_complete_checkpoint_and_ignores_progress_only_reports() -> None:
    root = _board()
    reports = [
        {"nodes": 10_000, "time": 50},
        _report(1, 10, CpScore(kind="cp", value=34), ["e2e4", "e7e5"], multipv=1),
        {"depth": 10, "nodes": 11_000, "currmove": "e2e4"},
        _report(2, 10, CpScore(kind="cp", value=10), ["d2d4", "d7d5"], multipv=2),
        _report(3, 10, CpScore(kind="cp", value=0), ["g1f3", "g8f6"], multipv=3),
        _report(1, 11, CpScore(kind="cp", value=40), ["e2e4", "c7c5"], multipv=1),
        _report(2, 11, CpScore(kind="cp", value=20), ["d2d4", "d7d5"], multipv=2),
        {"bestmove": "e2e4"},
    ]

    snapshot = collect_reports(reports, root, target_count=3)

    assert snapshot == CandidateSnapshot(
        depth=10,
        reports=[
            CandidateReport(rank=1, depth=10, score=CpScore(kind="cp", value=34), pv=["e2e4", "e7e5"]),
            CandidateReport(rank=2, depth=10, score=CpScore(kind="cp", value=10), pv=["d2d4", "d7d5"]),
            CandidateReport(rank=3, depth=10, score=CpScore(kind="cp", value=0), pv=["g1f3", "g8f6"]),
        ],
    )
    reports[1]["pv"][0] = "a2a4"
    assert snapshot.reports[0].pv[0] == "e2e4"


def test_incomplete_reports_do_not_backfill_across_packets() -> None:
    root = _board()
    reports = [
        _report(1, 9, CpScore(kind="cp", value=34), [], multipv=1),
        _report(1, 9, CpScore(kind="cp", value=34), ["e2e4", "e7e5"], multipv=1),
        _report(2, 9, CpScore(kind="cp", value=10), ["d2d4", "d7d5"], multipv=2),
        _report(3, 9, CpScore(kind="cp", value=0), ["g1f3", "g8f6"], multipv=3),
    ]

    snapshot = collect_reports(reports, root, target_count=3)

    assert snapshot.depth == 9
    assert [report.rank for report in snapshot.reports] == [1, 2, 3]
    assert snapshot.reports[0].pv == ["e2e4", "e7e5"]


def test_duplicate_root_or_rank_gap_invalidates_the_current_group() -> None:
    root = _board()
    reports = [
        _report(1, 10, CpScore(kind="cp", value=34), ["e2e4", "e7e5"], multipv=1),
        _report(2, 10, CpScore(kind="cp", value=20), ["e2e4", "c7c5"], multipv=2),
        _report(1, 11, CpScore(kind="cp", value=30), ["d2d4", "d7d5"], multipv=1),
        _report(3, 11, CpScore(kind="cp", value=10), ["g1f3", "g8f6"], multipv=3),
        _report(1, 12, CpScore(kind="cp", value=28), ["c2c4", "e7e5"], multipv=1),
        _report(2, 12, CpScore(kind="cp", value=12), ["d2d4", "d7d5"], multipv=2),
        _report(3, 12, CpScore(kind="cp", value=0), ["g1f3", "g8f6"], multipv=3),
    ]

    snapshot = collect_reports(reports, root, target_count=3)

    assert snapshot.depth == 12
    assert [report.rank for report in snapshot.reports] == [1, 2, 3]
    assert snapshot.reports[0].pv[0] == "c2c4"


def test_target_count_one_and_two_accept_exactly_the_required_ranks() -> None:
    root = _board()
    one = collect_reports([
        _report(1, 8, CpScore(kind="cp", value=5), ["e2e4"], multipv=1),
    ], root, target_count=1)
    two = collect_reports([
        _report(1, 8, CpScore(kind="cp", value=5), ["e2e4"], multipv=1),
        _report(2, 8, CpScore(kind="cp", value=2), ["d2d4"], multipv=2),
    ], root, target_count=2)

    assert len(one.reports) == 1
    assert len(two.reports) == 2
    assert one.reports[0].score == CpScore(kind="cp", value=5)
    assert two.reports[1].score == CpScore(kind="cp", value=2)


def test_bound_scores_are_rejected_if_no_complete_checkpoint_exists() -> None:
    root = _board()

    class FakeBoundScore:
        def pov(self, _color: chess.Color) -> "FakeBoundScore":
            return self

        def mate(self) -> None:
            return None

        def score(self, mate_score: int | None = None) -> int | None:
            return 7

        def is_lowerbound(self) -> bool:
            return True

    with pytest.raises(IncompleteCandidateSet) as excinfo:
        collect_reports([_report(1, 9, FakeBoundScore(), ["e2e4"], multipv=1)], root, target_count=1)

    assert excinfo.value.code == "INCOMPLETE_CANDIDATE_SET"


@pytest.mark.parametrize(
    ("score", "bound_name"),
    [
        (CpScore(kind="cp", value=7), "lowerbound"),
        ({"kind": "cp", "value": 7}, "upperbound"),
        (7, "lowerbound"),
    ],
)
def test_top_level_bound_flags_reject_fast_path_scores(score: object, bound_name: str) -> None:
    """Bounds on an info packet are authoritative for every score shape."""

    with pytest.raises(IncompleteCandidateSet) as excinfo:
        collect_reports(
            [
                _report(1, 9, score, ["e2e4"], multipv=1)
                | {bound_name: True},
            ],
            _board(),
            target_count=1,
        )

    assert excinfo.value.code == "INCOMPLETE_CANDIDATE_SET"


def test_mate_scores_are_accepted_in_rank_order() -> None:
    root = _board()
    snapshot = collect_reports(
        [
            _report(1, 9, MateScore(kind="mate", winner="white", moves=3), ["e2e4"], multipv=1),
            _report(2, 9, CpScore(kind="cp", value=12), ["d2d4"], multipv=2),
        ],
        root,
        target_count=2,
    )

    assert snapshot.depth == 9
    assert snapshot.reports[0].score == MateScore(kind="mate", winner="white", moves=3)


def test_last_complete_checkpoint_survives_later_incomplete_depths() -> None:
    root = _board()
    reports = [
        _report(1, 10, CpScore(kind="cp", value=34), ["e2e4"], multipv=1),
        _report(2, 10, CpScore(kind="cp", value=10), ["d2d4"], multipv=2),
        _report(3, 10, CpScore(kind="cp", value=0), ["g1f3"], multipv=3),
        _report(1, 11, CpScore(kind="cp", value=36), ["e2e4"], multipv=1),
        _report(2, 11, CpScore(kind="cp", value=12), ["d2d4"], multipv=2),
    ]

    snapshot = collect_reports(reports, root, target_count=3)

    assert snapshot.depth == 10
    assert [report.rank for report in snapshot.reports] == [1, 2, 3]
