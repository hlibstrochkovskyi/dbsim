"""Tests for conflict detection (M2.3)."""

from __future__ import annotations

from dbsim.analysis.conflicts import (
    OVERCAPACITY,
    SINGLE_TRACK_MEET,
    detect_conflicts,
    planned_occupations,
)
from dbsim.engine.meso import MesoCorridor, MesoSegment, MesoTrain


def _single(capacity: int, *, running: int = 600, headway: int = 120) -> MesoCorridor:
    return MesoCorridor(("A", "B"), (MesoSegment(0, "A-B", running, capacity, headway),))


def test_planned_occupations_are_uncontended() -> None:
    corridor = MesoCorridor(
        ("A", "B", "C"),
        (MesoSegment(0, "A-B", 300, 1, 60), MesoSegment(1, "B-C", 200, 1, 60)),
    )
    train = MesoTrain("T", (0, 1, 2), entry_time_s=100, dwell_s=30)
    occ = planned_occupations(corridor, [train])
    assert [(o.segment_index, o.enter_s, o.exit_s) for o in occ] == [
        (0, 100, 400),  # 100 + 300
        (1, 430, 630),  # 400 + 30 dwell, + 200
    ]


def test_injected_oversaturation_is_detected() -> None:
    # Three trains pile onto a single-track segment → over-saturation.
    corridor = _single(capacity=1, running=600)
    trains = [MesoTrain(f"T{i}", (0, 1), entry_time_s=i * 100) for i in range(3)]
    conflicts = detect_conflicts(corridor, planned_occupations(corridor, trains))
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.segment_index == 0
    assert c.kind == OVERCAPACITY
    assert c.peak_occupancy == 3
    assert c.trains == ("T0", "T1", "T2")
    assert c.start_s == 100  # the second train's entry begins the over-saturation


def test_opposing_single_track_is_a_meet() -> None:
    corridor = _single(capacity=1, running=300, headway=120)
    fwd = MesoTrain("FWD", (0, 1), entry_time_s=0)
    bwd = MesoTrain("BWD", (1, 0), entry_time_s=200)  # enters while FWD still blocks
    conflicts = detect_conflicts(corridor, planned_occupations(corridor, [fwd, bwd]))
    assert len(conflicts) == 1
    assert conflicts[0].kind == SINGLE_TRACK_MEET
    assert set(conflicts[0].trains) == {"FWD", "BWD"}


def test_double_track_tolerates_opposing_trains() -> None:
    corridor = _single(capacity=2, running=300, headway=60)
    fwd = MesoTrain("FWD", (0, 1), entry_time_s=0)
    bwd = MesoTrain("BWD", (1, 0), entry_time_s=100)
    # Two trains on a 2-track segment is within capacity → no conflict.
    assert detect_conflicts(corridor, planned_occupations(corridor, [fwd, bwd])) == []


def test_well_spaced_trains_have_no_conflict() -> None:
    corridor = _single(capacity=1, running=600, headway=120)
    trains = [MesoTrain("A", (0, 1), 0), MesoTrain("B", (0, 1), 1000)]
    assert detect_conflicts(corridor, planned_occupations(corridor, trains)) == []


def test_headway_violation_flagged_even_within_capacity() -> None:
    # Same direction, second enters just after the first clears but within headway.
    corridor = _single(capacity=1, running=300, headway=120)
    trains = [MesoTrain("A", (0, 1), 0), MesoTrain("B", (0, 1), 350)]  # gap 50 < headway 120
    conflicts = detect_conflicts(corridor, planned_occupations(corridor, trains))
    assert len(conflicts) == 1
    assert conflicts[0].kind == OVERCAPACITY  # same direction → not a "meet"
