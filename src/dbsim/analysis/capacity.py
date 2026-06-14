"""UIC 406 capacity analysis (M2.6).

The UIC 406 method measures how much of a line section's capacity a timetable
consumes. The idea is **blocking-time compression**: take the trains running over
a section in a time window, push their blocking times together until they just
touch (minimum headway, no buffer), and compare the resulting *infrastructure
occupation* to the window length.

    occupancy rate = compressed occupation time / time window

Here each train's minimum slot on a segment is ``running_time + headway`` (the
segment is blocked for the run, then a headway before the next train may enter);
a segment with ``capacity`` tracks divides the occupation by ``capacity``. The
analysis runs over the **busiest window** (auto-detected) and reports the
occupancy rate per segment, flags the **bottleneck** (highest rate), and compares
to a UIC threshold (≈ 0.75 for a mixed-traffic line; > 1.0 means over-saturated).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise

from dbsim.analysis.bildfahrplan import TrainPath
from dbsim.engine.meso import MesoCorridor

#: UIC 406 reference occupancy threshold for a mixed-traffic line (daily).
DEFAULT_THRESHOLD = 0.75


@dataclass(frozen=True, slots=True)
class SegmentOccupancy:
    """UIC 406 occupancy of one segment over the analysis window."""

    segment_index: int
    segment_name: str
    n_trains: int
    occupation_s: float
    occupancy_rate: float
    capacity: int


@dataclass(frozen=True, slots=True)
class CapacityReport:
    """A UIC 406 capacity-utilization report for a corridor."""

    window_start_s: int
    window_s: int
    threshold: float
    segments: tuple[SegmentOccupancy, ...]

    @property
    def bottleneck(self) -> SegmentOccupancy | None:
        return max(self.segments, key=lambda s: s.occupancy_rate, default=None)

    @property
    def over_threshold(self) -> bool:
        b = self.bottleneck
        return b is not None and b.occupancy_rate > self.threshold


def _interp_time_at(path: TrainPath, distance_km: float) -> int | None:
    """Interpolate the time at which a train passes ``distance_km`` along the corridor."""
    pts = sorted(path.points, key=lambda p: p[1])  # by distance
    lo, hi = pts[0][1], pts[-1][1]
    if distance_km < lo - 1e-6 or distance_km > hi + 1e-6:
        return None
    for (t0, d0), (t1, d1) in pairwise(pts):
        if d0 <= distance_km <= d1:
            if d1 == d0:
                return int(t0)
            frac = (distance_km - d0) / (d1 - d0)
            return round(t0 + (t1 - t0) * frac)
    return int(pts[-1][0])


def segment_entries_from_paths(
    station_distances_km: list[float], n_segments: int, paths: list[TrainPath]
) -> dict[int, list[int]]:
    """Per-segment train entry times, interpolated from Bildfahrplan train paths.

    A train uses segment *i* if its corridor span covers both station *i* and
    *i+1*; its entry time is when it reaches the nearer end (handles expresses
    that skip intermediate stations).
    """
    entries: dict[int, list[int]] = defaultdict(list)
    for path in paths:
        for i in range(n_segments):
            d_a, d_b = station_distances_km[i], station_distances_km[i + 1]
            t_a = _interp_time_at(path, d_a)
            t_b = _interp_time_at(path, d_b)
            if t_a is None or t_b is None:
                continue
            entries[i].append(min(t_a, t_b))
    return entries


def _peak_window(entries: dict[int, list[int]], window_s: int) -> int:
    """Find the window start (seconds) covering the most segment entries."""
    times = sorted(t for seg in entries.values() for t in seg)
    if not times:
        return 0
    best_start, best_count = times[0], 0
    right = 0
    for left, start in enumerate(times):
        right = max(right, left)
        while right < len(times) and times[right] < start + window_s:
            right += 1
        if right - left > best_count:
            best_count, best_start = right - left, start
    return best_start


def uic406_occupancy(
    corridor: MesoCorridor,
    segment_entries: dict[int, list[int]],
    *,
    window_s: int = 3600,
    window_start_s: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> CapacityReport:
    """Compute the UIC 406 occupancy rate per segment over the busiest window."""
    start = (
        window_start_s if window_start_s is not None else _peak_window(segment_entries, window_s)
    )
    end = start + window_s

    results: list[SegmentOccupancy] = []
    for seg in corridor.segments:
        in_window = [t for t in segment_entries.get(seg.index, []) if start <= t < end]
        slot = seg.running_time_s + seg.headway_s
        occupation = len(in_window) * slot / seg.capacity
        results.append(
            SegmentOccupancy(
                segment_index=seg.index,
                segment_name=seg.name,
                n_trains=len(in_window),
                occupation_s=occupation,
                occupancy_rate=occupation / window_s,
                capacity=seg.capacity,
            )
        )
    return CapacityReport(start, window_s, threshold, tuple(results))
