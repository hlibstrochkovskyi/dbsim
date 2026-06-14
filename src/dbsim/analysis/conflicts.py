"""Conflict detection on segment occupancy (M2.3).

Where the meso engine (M2.2) *resolves* contention by holding trains, this module
*detects* it: given trains' **planned** (uncontended) segment occupations — the
times each train would occupy each segment if it ran straight to schedule — it
finds where a segment is over-saturated.

Detection is blocking-time based. A train's *blocking interval* on a segment is
``[enter, exit + headway]``: the segment is reserved from when the train enters
until a headway after it clears (so a following train entering within the headway
overlaps, and is flagged). A **conflict** is a maximal time window where the
number of overlapping blocking intervals exceeds the segment's capacity. On a
single-track segment two opposing trains overlapping is the classic *meet*
conflict; on any segment, too many trains within headway is over-saturation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from dbsim.engine.meso import MesoCorridor, MesoTrain

# Conflict kinds.
SINGLE_TRACK_MEET = "single-track meet"
OVERCAPACITY = "overcapacity"


@dataclass(frozen=True, slots=True)
class Occupation:
    """A train's planned occupation of one segment (uncontended)."""

    train_id: str
    segment_index: int
    direction: int  # +1 / -1
    enter_s: int
    exit_s: int


@dataclass(frozen=True, slots=True)
class Conflict:
    """A detected over-saturation of a segment over a time window."""

    segment_index: int
    segment_name: str
    start_s: int
    end_s: int
    trains: tuple[str, ...]
    kind: str
    peak_occupancy: int
    capacity: int


def planned_occupations(corridor: MesoCorridor, trains: list[MesoTrain]) -> list[Occupation]:
    """Each train's segment intervals if it ran straight to schedule (no waiting)."""
    occupations: list[Occupation] = []
    for train in trains:
        t = train.entry_time_s
        for step in range(len(train.path) - 1):
            a, b = train.path[step], train.path[step + 1]
            seg = corridor.segments[min(a, b)]
            enter = t
            exit_s = enter + seg.running_time_s
            occupations.append(
                Occupation(train.train_id, seg.index, 1 if b > a else -1, enter, exit_s)
            )
            t = exit_s + train.dwell_s
    return occupations


def detect_conflicts(corridor: MesoCorridor, occupations: list[Occupation]) -> list[Conflict]:
    """Detect over-saturation windows per segment from planned occupations."""
    by_segment: dict[int, list[Occupation]] = defaultdict(list)
    for occ in occupations:
        by_segment[occ.segment_index].append(occ)

    conflicts: list[Conflict] = []
    for seg_index, occs in sorted(by_segment.items()):
        conflicts.extend(_detect_on_segment(corridor.segments[seg_index], occs))
    return conflicts


def _detect_on_segment(seg: object, occs: list[Occupation]) -> list[Conflict]:
    capacity = seg.capacity  # type: ignore[attr-defined]
    headway = seg.headway_s  # type: ignore[attr-defined]
    # Sweep over blocking-interval start/end events. At equal times, process ends
    # before starts so abutting intervals do not count as overlapping.
    events: list[tuple[int, int, Occupation]] = []
    for occ in occs:
        events.append((occ.enter_s, 1, occ))  # 1 = start
        events.append((occ.exit_s + headway, 0, occ))  # 0 = end (sorts first)
    events.sort(key=lambda e: (e[0], e[1]))

    conflicts: list[Conflict] = []
    active: set[Occupation] = set()
    window_start: int | None = None
    peak = 0
    involved: set[Occupation] = set()

    for time, is_start, occ in events:
        if is_start:
            active.add(occ)
        else:
            active.discard(occ)

        if len(active) > capacity:
            if window_start is None:
                window_start = time
            peak = max(peak, len(active))
            involved |= active
        elif window_start is not None:
            conflicts.append(_make_conflict(seg, window_start, time, involved, peak, capacity))
            window_start, peak, involved = None, 0, set()

    return conflicts


def _make_conflict(
    seg: object,
    start: int,
    end: int,
    involved: set[Occupation],
    peak: int,
    capacity: int,
) -> Conflict:
    directions = {o.direction for o in involved}
    kind = SINGLE_TRACK_MEET if capacity == 1 and len(directions) > 1 else OVERCAPACITY
    return Conflict(
        segment_index=seg.index,  # type: ignore[attr-defined]
        segment_name=seg.name,  # type: ignore[attr-defined]
        start_s=start,
        end_s=end,
        trains=tuple(sorted({o.train_id for o in involved})),
        kind=kind,
        peak_occupancy=peak,
        capacity=capacity,
    )
