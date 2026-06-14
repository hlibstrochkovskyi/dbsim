"""Tests for OSM ingestion and the track-segment model (M2.1).

Uses hand-built railway geometry (no network): a single track is one way; a
double track is two parallel ways ~12 m apart, which the cross-section method
must count as 2.
"""

from __future__ import annotations

from dbsim.ingest.osm import RailWay, bbox_around, parse_overpass
from dbsim.model.segments import (
    _projected_ways,
    build_corridor_segments,
    classify_segment,
    segment_graph,
)

# An east–west line near Tübingen; parallel tracks are offset in latitude.
LAT = 48.50
LON_W, LON_E = 9.00, 9.05
TRACK_OFFSET = 0.00011  # ~12 m in latitude


def _straight_way(osm_id: int, lat: float, **tags: object) -> RailWay:
    return RailWay(
        osm_id=osm_id,
        ref=str(tags.get("ref", "100")),
        usage="main",
        tracks=tags.get("tracks"),  # type: ignore[arg-type]
        electrified=bool(tags.get("electrified", True)),
        max_speed_kmh=tags.get("max_speed_kmh"),  # type: ignore[arg-type]
        preferred_direction=bool(tags.get("preferred_direction", False)),
        geometry=((lat, LON_W), (lat, LON_E)),
    )


_A = ("A", LAT, LON_W + 0.001)
_B = ("B", LAT, LON_E - 0.001)


def test_parse_overpass_reads_tags() -> None:
    data = {
        "elements": [
            {
                "type": "way",
                "id": 42,
                "geometry": [{"lat": 48.5, "lon": 9.0}, {"lat": 48.5, "lon": 9.1}],
                "tags": {
                    "railway": "rail",
                    "ref": "3600",
                    "usage": "main",
                    "tracks": "2",
                    "electrified": "contact_line",
                    "maxspeed": "160",
                    "railway:preferred_direction": "forward",
                },
            }
        ]
    }
    ways = parse_overpass(data)
    assert len(ways) == 1
    w = ways[0]
    assert (w.osm_id, w.ref, w.usage, w.tracks) == (42, "3600", "main", 2)
    assert w.electrified and w.preferred_direction
    assert w.max_speed_kmh == 160


def test_parse_overpass_skips_short_geometry() -> None:
    data = {"elements": [{"type": "way", "id": 1, "geometry": [{"lat": 1, "lon": 2}]}]}
    assert parse_overpass(data) == []


def test_bbox_around() -> None:
    south, west, north, east = bbox_around([(48.5, 9.0), (48.6, 9.2)], margin_deg=0.01)
    assert (round(south, 2), round(west, 2), round(north, 2), round(east, 2)) == (
        48.49,
        8.99,
        48.61,
        9.21,
    )


def test_single_track_one_way() -> None:
    ways = [_straight_way(1, LAT, ref="4633", max_speed_kmh=100)]
    seg = classify_segment("A", "B", (_A[1], _A[2]), (_B[1], _B[2]), _projected_ways(ways))
    assert seg.tracks == 1
    assert seg.single_track
    assert seg.line_ref == "4633"
    assert seg.max_speed_kmh == 100
    assert 3.0 < seg.length_km < 4.0  # ~3.7 km east–west span


def test_double_track_two_parallel_ways() -> None:
    ways = [
        _straight_way(1, LAT, ref="3600"),
        _straight_way(2, LAT + TRACK_OFFSET, ref="3600"),
    ]
    seg = classify_segment("A", "B", (_A[1], _A[2]), (_B[1], _B[2]), _projected_ways(ways))
    assert seg.tracks == 2
    assert not seg.single_track


def test_quad_track_four_parallel_ways() -> None:
    ways = [_straight_way(i, LAT + i * TRACK_OFFSET, ref="3600") for i in range(4)]
    seg = classify_segment("A", "B", (_A[1], _A[2]), (_B[1], _B[2]), _projected_ways(ways))
    assert seg.tracks == 4


def test_tracks_tag_is_honoured() -> None:
    # A single way carrying two tracks (tracks=2) counts as 2.
    ways = [_straight_way(1, LAT, ref="3600", tracks=2)]
    seg = classify_segment("A", "B", (_A[1], _A[2]), (_B[1], _B[2]), _projected_ways(ways))
    assert seg.tracks == 2


def test_no_rail_returns_zero_tracks() -> None:
    seg = classify_segment("A", "B", (_A[1], _A[2]), (_B[1], _B[2]), [])
    assert seg.tracks == 0
    assert seg.line_ref is None


def test_build_corridor_segments_and_graph() -> None:
    ways = [_straight_way(1, LAT, ref="4633")]
    stations = [
        ("A", LAT, LON_W + 0.001),
        ("M", LAT, (LON_W + LON_E) / 2),
        ("B", LAT, LON_E - 0.001),
    ]
    segments = build_corridor_segments(stations, ways)
    assert len(segments) == 2
    assert all(s.single_track for s in segments)

    graph = segment_graph(segments)
    assert graph.num_nodes() == 3
    assert graph.num_edges() == 2
