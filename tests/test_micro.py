"""Tests for the microscopic infrastructure model (M3.1).

Uses the committed-by-reference OSM caches for the Pfäffingen loop where present;
otherwise builds the zone from a small synthetic OSM feature set, so the test runs
with no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbsim.ingest import fetch_railway_features, fetch_railways
from dbsim.ingest.osm import RailWay, RailwayFeatures, RailwayNode
from dbsim.model import curate_pfaffingen_loop
from dbsim.model.micro import APPROACH, LOOP, Block, MicroRoute, MicroZone

_CACHE_RAIL = Path("data/raw/osm/micro-pfaeffingen-rail.json")
_CACHE_FEATURES = Path("data/raw/osm/survey-pfaeffingen-loop.json")


def _synthetic() -> tuple[list[RailWay], RailwayFeatures]:
    # Two switches ~289 m apart; rail ways at 70 km/h (mode) and a 50 km/h loop track.
    ways = [
        RailWay(1, "4633", "branch", None, True, 70, False, ((48.531, 8.966), (48.533, 8.963))),
        RailWay(2, "4633", "branch", None, True, 70, False, ((48.531, 8.966), (48.532, 8.965))),
        RailWay(3, None, None, None, True, 50, False, ((48.5315, 8.9655), (48.5325, 8.9640))),
    ]
    switches = (
        RailwayNode(10, "switch", 48.5313, 8.9660, None, None),
        RailwayNode(11, "switch", 48.5332, 8.9633, None, None),
    )
    signals = (
        RailwayNode(20, "signal", 48.531, 8.966, "main", "forward"),
        RailwayNode(21, "signal", 48.533, 8.963, "main", "backward"),
    )
    return ways, RailwayFeatures(signals, switches, (), ())


def _zone() -> MicroZone:
    if _CACHE_RAIL.exists() and _CACHE_FEATURES.exists():
        ways = fetch_railways((0, 0, 0, 0), cache_path=_CACHE_RAIL)
        feats = fetch_railway_features((0, 0, 0, 0), cache_path=_CACHE_FEATURES)
    else:
        ways, feats = _synthetic()
    return curate_pfaffingen_loop(ways, feats)


def test_curated_zone_is_a_valid_passing_loop() -> None:
    zone = _zone()
    assert zone.validate() == []
    assert zone.name == "Pfäffingen"
    assert (zone.west_boundary, zone.east_boundary) == ("Unterjesingen Mitte", "Entringen")


def test_zone_has_two_loop_tracks_and_two_approaches() -> None:
    zone = _zone()
    assert len(zone.loop_blocks) == 2
    assert len(zone.approach_blocks) == 2
    assert sum(b.has_platform for b in zone.blocks) >= 1  # a platform track exists


def test_loop_length_grounded_in_switch_span() -> None:
    zone = _zone()
    loop = zone.block("loop_t1")
    # Pfäffingen's loop is ~289 m (switch-to-switch); allow a generous band.
    assert 200 < loop.length_m < 400


def test_passing_track_is_slower_than_through_track() -> None:
    zone = _zone()
    assert zone.block("loop_t2").max_speed_kmh < zone.block("loop_t1").max_speed_kmh


def test_four_routes_two_directions_two_tracks() -> None:
    zone = _zone()
    assert len(zone.routes) == 4
    directions = {(r.direction, r.loop_track) for r in zone.routes}
    assert directions == {
        ("west_to_east", "1"),
        ("west_to_east", "2"),
        ("east_to_west", "1"),
        ("east_to_west", "2"),
    }


def test_signals_carried_from_osm() -> None:
    zone = _zone()
    assert len(zone.signals) >= 2


def test_validate_catches_broken_route() -> None:
    bad = MicroZone(
        name="bad",
        west_boundary="A",
        east_boundary="B",
        blocks=(
            Block("appr_w", 500, 70, False, APPROACH),
            Block("t1", 289, 70, True, LOOP),
            Block("t2", 289, 50, False, LOOP),
            Block("appr_e", 500, 70, False, APPROACH),
        ),
        routes=(MicroRoute("r", "west_to_east", "1", ("appr_w", "ghost", "appr_e")),),
        signals=(),
        switches=(),
    )
    issues = bad.validate()
    assert any("unknown blocks" in i for i in issues)


def test_block_lookup_raises_for_missing() -> None:
    with pytest.raises(KeyError):
        _zone().block("nope")
