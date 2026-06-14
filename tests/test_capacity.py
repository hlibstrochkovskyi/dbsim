"""Tests for UIC 406 capacity analysis (M2.6)."""

from __future__ import annotations

from dbsim.analysis.bildfahrplan import DOWN, TrainPath
from dbsim.analysis.capacity import (
    segment_entries_from_paths,
    uic406_occupancy,
)
from dbsim.engine.meso import MesoCorridor, MesoSegment


def _corridor() -> MesoCorridor:
    # Two single-track segments (long + short) and one double-track.
    return MesoCorridor(
        ("A", "B", "C", "D"),
        (
            MesoSegment(0, "A-B", 280, 1, 120),  # slot 400 s
            MesoSegment(1, "B-C", 80, 1, 120),  # slot 200 s
            MesoSegment(2, "C-D", 280, 2, 120),  # slot 400 s, capacity 2
        ),
    )


def test_occupancy_math_and_bottleneck() -> None:
    corridor = _corridor()
    # 9 trains enter each segment within one hour.
    entries = {
        0: list(range(0, 5400, 600)),
        1: list(range(0, 5400, 600)),
        2: list(range(0, 5400, 600)),
    }
    report = uic406_occupancy(corridor, entries, window_s=3600, window_start_s=0)
    rates = {s.segment_index: round(s.occupancy_rate, 3) for s in report.segments}
    # seg0: 6 trains * 400 / 3600 ; seg1: 6 * 200 / 3600 ; seg2: 6 * 400 / 2 / 3600
    assert rates[0] == round(6 * 400 / 3600, 3)
    assert rates[1] == round(6 * 200 / 3600, 3)
    assert rates[2] == round(6 * 400 / 2 / 3600, 3)
    # The long single-track segment is the bottleneck.
    assert report.bottleneck is not None
    assert report.bottleneck.segment_index == 0


def test_capacity_halves_occupancy() -> None:
    corridor = _corridor()
    entries = {0: [0, 600], 2: [0, 600]}  # same trains on seg0 (cap 1) and seg2 (cap 2)
    report = uic406_occupancy(corridor, entries, window_s=3600, window_start_s=0)
    rate = {s.segment_index: s.occupancy_rate for s in report.segments}
    assert rate[2] == rate[0] / 2  # double track → half the occupancy


def test_over_threshold_flag() -> None:
    corridor = _corridor()
    entries = {0: list(range(0, 3600, 360))}  # 10 trains on seg0 → 10*400/3600 ≈ 1.11
    report = uic406_occupancy(corridor, entries, window_s=3600, window_start_s=0, threshold=0.75)
    assert report.over_threshold
    assert report.bottleneck is not None and report.bottleneck.occupancy_rate > 1.0


def test_peak_window_is_auto_detected() -> None:
    corridor = _corridor()
    # Two trains at night, a burst of four around t=10000.
    entries = {0: [0, 100, 10000, 10100, 10200, 10300]}
    report = uic406_occupancy(corridor, entries, window_s=3600)
    assert report.window_start_s == 10000  # the busy window is chosen
    assert report.segments[0].n_trains == 4


def test_segment_entries_interpolated_from_paths() -> None:
    # Station distances 0, 10, 30 km. A down train passing 0 km at t=0, 30 km at t=300.
    station_dists = [0.0, 10.0, 30.0]
    path = TrainPath("T", "ICE", points=((0, 0.0), (300, 30.0)), direction=DOWN)
    entries = segment_entries_from_paths(station_dists, n_segments=2, paths=[path])
    # seg0 spans 0–10 km → entry at the 0 km end (t=0).
    assert entries[0] == [0]
    # seg1 spans 10–30 km → entry when it reaches 10 km: t = 300 * 10/30 = 100.
    assert entries[1] == [100]


def test_express_train_using_a_segment_it_skips() -> None:
    # An express calls only at 0 km and 30 km but still occupies the middle segment.
    station_dists = [0.0, 10.0, 30.0]
    path = TrainPath("X", "ICE", points=((0, 0.0), (600, 30.0)), direction=DOWN)
    entries = segment_entries_from_paths(station_dists, n_segments=2, paths=[path])
    assert 1 in entries and len(entries[1]) == 1
