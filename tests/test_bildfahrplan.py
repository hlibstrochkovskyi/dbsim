"""Tests for the Bildfahrplan layer (:mod:`dbsim.analysis.bildfahrplan`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbsim.analysis.bildfahrplan import (
    DOWN,
    UP,
    Corridor,
    TrainPath,
    _haversine_km,
    build_corridor,
    extract_train_paths,
    render_bildfahrplan,
)
from dbsim.model import Timetable

# A corridor over the gtfs_mini fixture: Nordstadt (north) → Suedstadt (south).
MINI_CORRIDOR = ("Nordstadt", "Musterstadt Hbf", "Suedstadt")
WEDNESDAY = 20260617
H = 3600


def _ice_path(mini_tt: Timetable) -> tuple[Corridor, TrainPath]:
    corridor = build_corridor(mini_tt, MINI_CORRIDOR)
    paths = extract_train_paths(mini_tt, corridor, WEDNESDAY)
    ice = next(p for p in paths if p.trip_id == "T_ICE_A")
    return corridor, ice


def test_haversine_basics() -> None:
    assert _haversine_km(50.0, 8.0, 50.0, 8.0) == pytest.approx(0.0)
    # One degree of latitude is ~111 km.
    assert _haversine_km(50.0, 8.0, 51.0, 8.0) == pytest.approx(111.2, abs=1.0)


def test_corridor_distances_are_monotonic(mini_tt: Timetable) -> None:
    corridor = build_corridor(mini_tt, MINI_CORRIDOR)
    dists = [s.distance_km for s in corridor.stations]
    assert dists[0] == 0.0
    assert dists == sorted(dists)
    assert dists[1] < dists[2]


def test_corridor_includes_platform_children(mini_tt: Timetable) -> None:
    corridor = build_corridor(mini_tt, MINI_CORRIDOR)
    hbf = corridor.stations[1]
    assert {"S_HBF", "S_HBF_1"} <= hbf.stop_ids


def test_build_corridor_requires_two_stations(mini_tt: Timetable) -> None:
    with pytest.raises(ValueError, match="at least two"):
        build_corridor(mini_tt, ("Nordstadt",))


def test_unknown_station_raises(mini_tt: Timetable) -> None:
    with pytest.raises(ValueError, match="not found"):
        build_corridor(mini_tt, ("Nordstadt", "Nowhere"))


def test_extract_paths_directions(mini_tt: Timetable) -> None:
    corridor = build_corridor(mini_tt, MINI_CORRIDOR)
    paths = {p.trip_id: p for p in extract_train_paths(mini_tt, corridor, WEDNESDAY)}
    assert set(paths) == {"T_ICE_A", "T_NIGHT"}
    # T_ICE_A runs Nordstadt -> Suedstadt (increasing distance).
    assert paths["T_ICE_A"].direction == DOWN
    # T_NIGHT runs Suedstadt -> Nordstadt (decreasing distance).
    assert paths["T_NIGHT"].direction == UP


def test_train_path_points_sorted_and_origin_correct(mini_tt: Timetable) -> None:
    _corridor, ice = _ice_path(mini_tt)
    times = [t for t, _ in ice.points]
    assert times == sorted(times)
    # Departs Nordstadt (distance 0) at 08:00.
    assert ice.points[0] == (8 * H, 0.0)


def test_dwell_creates_two_points_at_a_station(mini_tt: Timetable) -> None:
    corridor, ice = _ice_path(mini_tt)
    hbf_dist = corridor.stations[1].distance_km
    at_hbf = [t for t, d in ice.points if d == hbf_dist]
    # Arrival 09:00 and departure 09:05 → two distinct points at the Hbf.
    assert at_hbf == [9 * H, 9 * H + 5 * 60]


def test_render_writes_png(mini_tt: Timetable, tmp_path: Path) -> None:
    corridor = build_corridor(mini_tt, MINI_CORRIDOR)
    paths = extract_train_paths(mini_tt, corridor, WEDNESDAY)
    out = render_bildfahrplan(corridor, paths, tmp_path / "bild.png", title="test")
    assert out.exists()
    assert out.stat().st_size > 0


def test_render_handles_empty_paths(mini_tt: Timetable, tmp_path: Path) -> None:
    corridor = build_corridor(mini_tt, MINI_CORRIDOR)
    out = render_bildfahrplan(corridor, [], tmp_path / "empty.png")
    assert out.exists()
