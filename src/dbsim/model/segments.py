"""Track-segment infrastructure model from OSM (M2.1).

Turns the OSM ``railway=rail`` ways (one way per physical track) into
**station-to-station segments** with capacity attributes — chiefly the number of
tracks (single vs double), plus electrification and line speed.

Track count is measured geometrically by the **cross-section method**: along the
line connecting two stations, sample points and cast a short line *perpendicular*
to the local track direction; the number of parallel tracks it crosses is the
track count there. Taking the median over several samples (away from the station
throats, where extra platform/siding tracks fan out) gives a robust single-vs-
double classification — and, since the `tracks` tag is rarely present in OSM, it
does not depend on tagging.

Classification is done per line ``ref`` (Streckennummer): the segment's *dominant*
line is the one with the most track length in the corridor, so a double-track
main line beside a separate S-Bahn line is reported as double (for that line),
not quadruple.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise

import rustworkx as rx
from pyproj import Transformer
from shapely.geometry import LineString, Point

from dbsim.ingest.osm import RailWay

# Project WGS84 → ETRS89 / UTM 32N (metres), which covers Germany.
_TF = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)


def _project(lat: float, lon: float) -> tuple[float, float]:
    x, y = _TF.transform(lon, lat)
    return (x, y)


@dataclass(frozen=True, slots=True)
class Segment:
    """A station-to-station track segment with capacity attributes."""

    from_station: str
    to_station: str
    length_km: float
    tracks: int
    line_ref: str | None
    electrified: bool
    max_speed_kmh: int | None

    @property
    def single_track(self) -> bool:
        return self.tracks <= 1


def _projected_ways(ways: list[RailWay]) -> list[tuple[RailWay, LineString]]:
    out: list[tuple[RailWay, LineString]] = []
    for w in ways:
        line = LineString([_project(lat, lon) for lat, lon in w.geometry])
        if line.length > 0:
            out.append((w, line))
    return out


def _dominant_ref(cands: list[tuple[RailWay, LineString]], buffer: object) -> str | None:
    """The line ``ref`` with the most track length in the corridor buffer."""
    length_by_ref: dict[str | None, float] = defaultdict(float)
    for w, line in cands:
        length_by_ref[w.ref] += line.intersection(buffer).length
    refs = {r: ln for r, ln in length_by_ref.items() if r is not None}
    if refs:
        return max(refs, key=lambda r: refs[r])
    return None


def _count_tracks(
    chord: LineString,
    ref_pairs: list[tuple[RailWay, LineString]],
    *,
    n_samples: int,
    half_width_m: float,
    max_offset_m: float,
) -> int:
    """Median track count over perpendicular cross-sections along the chord.

    At each sample point on the straight chord, find the nearest rail line, cast a
    short line perpendicular to that line's *local* direction, and count the
    parallel tracks it crosses (weighting by a way's ``tracks`` tag if present).
    """
    counts: list[int] = []
    for i in range(1, n_samples + 1):
        p = chord.interpolate(i / (n_samples + 1), normalized=True)
        nearest_line: LineString | None = None
        nearest_pt: Point | None = None
        nearest_d = 0.0
        best = math.inf
        for _w, line in ref_pairs:
            d = line.project(p)
            pt = line.interpolate(d)
            dist = p.distance(pt)
            if dist < best:
                best, nearest_line, nearest_pt, nearest_d = dist, line, pt, d
        if nearest_line is None or nearest_pt is None or best > max_offset_m:
            continue
        p1 = nearest_line.interpolate(max(0.0, nearest_d - 5.0))
        p2 = nearest_line.interpolate(min(nearest_line.length, nearest_d + 5.0))
        dx, dy = p2.x - p1.x, p2.y - p1.y
        norm = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / norm, dx / norm  # unit perpendicular to local track
        cx, cy = nearest_pt.x, nearest_pt.y
        cross = LineString(
            [
                (cx - nx * half_width_m, cy - ny * half_width_m),
                (cx + nx * half_width_m, cy + ny * half_width_m),
            ]
        )
        counts.append(sum((w.tracks or 1) for w, line in ref_pairs if cross.intersects(line)))
    positive = [c for c in counts if c > 0]
    return int(statistics.median(positive)) if positive else 0


def classify_segment(
    from_name: str,
    to_name: str,
    a_latlon: tuple[float, float],
    b_latlon: tuple[float, float],
    projected_ways: list[tuple[RailWay, LineString]],
    *,
    corridor_buffer_m: float = 250.0,
    half_width_m: float = 80.0,
    n_samples: int = 11,
) -> Segment:
    """Classify the track segment between two stations from OSM ways."""
    a = Point(_project(*a_latlon))
    b = Point(_project(*b_latlon))
    chord = LineString([a, b])
    buffer = chord.buffer(corridor_buffer_m)

    cands = [(w, line) for (w, line) in projected_ways if line.intersects(buffer)]
    length_km = chord.length / 1000.0
    if not cands:
        return Segment(from_name, to_name, length_km, 0, None, False, None)

    ref = _dominant_ref(cands, buffer)
    ref_pairs = [(w, line) for (w, line) in cands if w.ref == ref]
    if not ref_pairs:
        ref_pairs = cands

    tracks = _count_tracks(
        chord,
        ref_pairs,
        n_samples=n_samples,
        half_width_m=half_width_m,
        max_offset_m=corridor_buffer_m,
    )

    electrified = any(w.electrified for w, _ in ref_pairs)
    speeds = [w.max_speed_kmh for w, _ in ref_pairs if w.max_speed_kmh]
    return Segment(
        from_station=from_name,
        to_station=to_name,
        length_km=length_km,
        tracks=tracks,
        line_ref=ref,
        electrified=electrified,
        max_speed_kmh=max(speeds) if speeds else None,
    )


def build_corridor_segments(
    stations: list[tuple[str, float, float]], ways: list[RailWay]
) -> list[Segment]:
    """Build segments for each consecutive station pair along a corridor.

    Args:
        stations: ordered ``(name, lat, lon)`` tuples.
        ways: OSM rail ways covering the corridor.
    """
    projected = _projected_ways(ways)
    segments: list[Segment] = []
    for (a_name, a_lat, a_lon), (b_name, b_lat, b_lon) in pairwise(stations):
        segments.append(classify_segment(a_name, b_name, (a_lat, a_lon), (b_lat, b_lon), projected))
    return segments


def segment_graph(segments: list[Segment]) -> rx.PyDiGraph[str, Segment]:
    """A directed station graph with :class:`Segment`s as edge payloads."""
    graph: rx.PyDiGraph[str, Segment] = rx.PyDiGraph()
    index: dict[str, int] = {}

    def node(name: str) -> int:
        if name not in index:
            index[name] = graph.add_node(name)
        return index[name]

    for seg in segments:
        graph.add_edge(node(seg.from_station), node(seg.to_station), seg)
    return graph
