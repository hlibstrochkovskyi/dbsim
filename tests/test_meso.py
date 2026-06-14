"""Tests for the mesoscopic segment-occupancy model (M2.2)."""

from __future__ import annotations

from dbsim.engine.meso import (
    MesoCorridor,
    MesoSegment,
    MesoSimulation,
    MesoTrain,
    meso_corridor_from_segments,
)
from dbsim.model.segments import Segment
from dbsim.record import hash_run


def _single_segment(capacity: int, *, running: int = 600, headway: int = 120) -> MesoCorridor:
    return MesoCorridor(("A", "B"), (MesoSegment(0, "A-B", running, capacity, headway),))


def test_single_track_excludes_opposing_trains() -> None:
    # The M2.2 acceptance: two trains cannot share a single-track segment.
    corridor = _single_segment(capacity=1)
    fwd = MesoTrain("FWD", (0, 1), entry_time_s=0, priority=1)
    bwd = MesoTrain("BWD", (1, 0), entry_time_s=0, priority=0)
    meso = MesoSimulation(corridor, [fwd, bwd])
    meso.run()

    assert meso.max_occupancy(0) == 1
    assert meso.overcapacity_segments() == []
    intervals = {o.train_id: (o.enter_s, o.exit_s) for o in meso.occupancy}
    # The opposing train only enters after the first has cleared the segment.
    assert intervals["FWD"] == (0, 600)
    assert intervals["BWD"][0] >= intervals["FWD"][1]


def test_double_track_allows_two_at_once() -> None:
    corridor = _single_segment(capacity=2)
    meso = MesoSimulation(
        corridor,
        [MesoTrain("FWD", (0, 1), 0, 1), MesoTrain("BWD", (1, 0), 0, 0)],
    )
    meso.run()
    assert meso.max_occupancy(0) == 2
    assert meso.overcapacity_segments() == []


def test_headway_separates_following_trains() -> None:
    # Two same-direction trains on a single track keep the headway between entries.
    corridor = _single_segment(capacity=1, running=300, headway=120)
    meso = MesoSimulation(
        corridor,
        [MesoTrain("A", (0, 1), 0, 2), MesoTrain("B", (0, 1), 0, 1)],
    )
    meso.run()
    enters = sorted(o.enter_s for o in meso.occupancy)
    # First at 0; second cannot enter until the first clears (300) — well past headway.
    assert enters[0] == 0
    assert enters[1] >= 300


def test_meet_happens_at_a_station() -> None:
    # 3 stations, 2 single-track segments. Opposing trains meet at the middle.
    corridor = MesoCorridor(
        ("A", "B", "C"),
        (MesoSegment(0, "A-B", 300, 1, 60), MesoSegment(1, "B-C", 300, 1, 60)),
    )
    fwd = MesoTrain("FWD", (0, 1, 2), 0, 1)
    bwd = MesoTrain("BWD", (2, 1, 0), 0, 0)
    meso = MesoSimulation(corridor, [fwd, bwd])
    meso.run()
    assert meso.overcapacity_segments() == []
    assert meso.max_occupancy(0) == 1
    assert meso.max_occupancy(1) == 1


def test_deterministic() -> None:
    corridor = _single_segment(capacity=1)
    trains = [MesoTrain("FWD", (0, 1), 0, 1), MesoTrain("BWD", (1, 0), 0, 0)]
    a = MesoSimulation(corridor, trains).run()
    b = MesoSimulation(corridor, trains).run()
    assert hash_run(a) == hash_run(b)


def test_corridor_from_segments() -> None:
    segments = [
        Segment(
            "A", "B", length_km=10.0, tracks=1, line_ref="4633", electrified=True, max_speed_kmh=100
        ),
        Segment(
            "B", "C", length_km=20.0, tracks=2, line_ref="4860", electrified=True, max_speed_kmh=120
        ),
    ]
    corridor = meso_corridor_from_segments(segments, headway_s=90)
    assert corridor.stations == ("A", "B", "C")
    assert corridor.segments[0].capacity == 1
    assert corridor.segments[1].capacity == 2
    # 10 km at 100 km/h = 360 s.
    assert corridor.segments[0].running_time_s == 360
    assert corridor.segments[1].headway_s == 90
