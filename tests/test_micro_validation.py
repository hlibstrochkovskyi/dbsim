"""Tests for the micro-validation harness (M3.5)."""

from __future__ import annotations

from dbsim.analysis.micro_validation import micro_min_headway_s, validate_micro_zone
from dbsim.engine.blocking import TrainDynamics
from dbsim.engine.coupling import BoundaryArrival
from dbsim.model.micro import APPROACH, LOOP, Block, MicroRoute, MicroZone


def _zone() -> MicroZone:
    return MicroZone(
        name="loop",
        west_boundary="W",
        east_boundary="E",
        blocks=(
            Block("west_approach", 500, 70, False, APPROACH),
            Block("loop_t1", 300, 70, True, LOOP),
            Block("loop_t2", 300, 50, False, LOOP),
            Block("east_approach", 500, 70, False, APPROACH),
        ),
        routes=(
            MicroRoute("WE_t1", "west_to_east", "1", ("west_approach", "loop_t1", "east_approach")),
        ),
        signals=(),
        switches=(),
    )


def test_micro_min_headway_is_positive() -> None:
    h = micro_min_headway_s(_zone(), TrainDynamics())
    assert h > 0


def test_well_spaced_timetable_passes() -> None:
    # Trains far apart in both directions → never over-occupied, consistent.
    arrivals = [
        BoundaryArrival("a", "WE", 0),
        BoundaryArrival("b", "EW", 1000),
        BoundaryArrival("c", "WE", 2000),
    ]
    report = validate_micro_zone(_zone(), arrivals, 20260616)
    assert report.passes
    assert report.n_trains == 3
    assert report.max_occupancy <= report.capacity


def test_meet_counted_and_occupancy_bounded() -> None:
    # Two opposing trains arriving together → a meet, occupancy 2 (the 2 tracks).
    arrivals = [BoundaryArrival("we", "WE", 0), BoundaryArrival("ew", "EW", 5)]
    report = validate_micro_zone(_zone(), arrivals, 20260616)
    assert report.n_meets == 1
    assert report.max_occupancy == 2
    assert report.capacity == 2
    assert report.occupancy_ok and report.passes


def test_utilisation_reflects_spacing() -> None:
    # Two same-direction trains a long way apart → low utilisation.
    arrivals = [BoundaryArrival("a", "WE", 0), BoundaryArrival("b", "WE", 3600)]
    report = validate_micro_zone(_zone(), arrivals, 20260616)
    assert 0 < report.utilisation < 1.0  # micro headway ≪ observed spacing
    assert report.min_observed_headway_s == 3600


def test_report_is_deterministic() -> None:
    arrivals = [BoundaryArrival("a", "WE", 0), BoundaryArrival("b", "EW", 10)]
    assert validate_micro_zone(_zone(), arrivals, 20260616) == validate_micro_zone(
        _zone(), arrivals, 20260616
    )
