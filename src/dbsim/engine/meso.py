"""Mesoscopic segment-occupancy simulation (M2.2).

The macro engine (M1.1/M1.2) moves trains stop-to-stop without contention. The
**meso** engine adds the track between stations as a *contended resource*: a
segment has a capacity (its number of tracks) and a minimum **headway**, and a
train must acquire the segment before it can run over it. If the segment is full
(or the headway since the last entry has not elapsed) the train **waits at the
station** — so two trains cannot occupy a single-track segment at the same time
(in either direction), and following trains keep their headway.

This is the principled home for the headway deferred from M1.2: contention only
makes sense once a track-occupancy model exists (M2.1's segments).

Conflict *detection/reporting* is M2.3; a smart *dispatcher* to resolve contention
by policy is M2.4. Here the rule is simply priority-then-FIFO, and trains meet/
pass at stations. (Network-wide deadlock avoidance across many single-track
segments is M3.3; a single meet does not deadlock.)
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from dbsim.engine.events import Event
from dbsim.engine.loop import RunResult, Simulation
from dbsim.model.segments import Segment

_READY = "ready"  # internal: train at a station, ready to take the next segment
_ARRIVE = "arrive"  # train reaches the next station (also the movement-record label)
_TRY = "try"  # internal: re-evaluate a segment's queue when headway elapses
_DEPART = "depart"  # movement-record label: train actually entered the segment


@dataclass(frozen=True, slots=True)
class MesoSegment:
    """A track segment between two adjacent corridor stations."""

    index: int  # connects station `index` and `index + 1`
    name: str
    running_time_s: int
    capacity: int  # number of tracks (1 = single-track)
    headway_s: int


@dataclass(frozen=True, slots=True)
class MesoCorridor:
    """An ordered chain of stations with the segments between them."""

    stations: tuple[str, ...]
    segments: tuple[MesoSegment, ...]  # len == len(stations) - 1

    def station_index(self, name: str) -> int:
        return self.stations.index(name)


@dataclass(frozen=True, slots=True)
class MesoTrain:
    """A train traversing the corridor along an ordered list of station indices."""

    train_id: str
    path: tuple[int, ...]  # station indices in visiting order (may ascend or descend)
    entry_time_s: int  # when it is ready to leave its first station
    priority: int = 0  # higher wins contention
    dwell_s: int = 30


@dataclass(frozen=True, slots=True)
class OccupancyRecord:
    """A train's occupation of a segment over a time interval."""

    segment_index: int
    train_id: str
    direction: int  # +1 ascending station index, -1 descending
    enter_s: int
    exit_s: int


@dataclass(frozen=True, slots=True)
class MovementRecord:
    """A meso movement event (depart a station / arrive at a station)."""

    time_s: int
    train_id: str
    station_index: int
    event: str  # _READY (depart) or _ARRIVE


@dataclass(order=True, slots=True)
class _Waiter:
    """A queued segment request, ordered by priority then arrival sequence."""

    sort_key: tuple[int, int]
    train_id: str = field(compare=False)
    step: int = field(compare=False)


def meso_corridor_from_segments(
    segments: list[Segment],
    *,
    headway_s: int = 120,
    default_speed_kmh: int = 80,
    min_running_s: int = 60,
) -> MesoCorridor:
    """Build a :class:`MesoCorridor` from M2.1 track :class:`Segment`s.

    Running time is the technical traversal time (length / line speed); capacity
    is the track count (a no-rail segment falls back to single-track).
    """
    if not segments:
        raise ValueError("need at least one segment")
    stations = [segments[0].from_station, *(s.to_station for s in segments)]
    meso_segments = []
    for i, s in enumerate(segments):
        speed = s.max_speed_kmh or default_speed_kmh
        running = max(min_running_s, round(s.length_km / speed * 3600))
        meso_segments.append(
            MesoSegment(
                index=i,
                name=f"{s.from_station}–{s.to_station}",
                running_time_s=running,
                capacity=max(1, s.tracks),
                headway_s=headway_s,
            )
        )
    return MesoCorridor(tuple(stations), tuple(meso_segments))


def _segment_for(a: int, b: int) -> int:
    return min(a, b)


def _direction(a: int, b: int) -> int:
    return 1 if b > a else -1


class MesoSimulation:
    """Runs :class:`MesoTrain`s over a :class:`MesoCorridor` with contention."""

    def __init__(self, corridor: MesoCorridor, trains: list[MesoTrain], *, seed: int = 0) -> None:
        self.corridor = corridor
        self._trains = {t.train_id: t for t in trains}
        n = len(corridor.segments)
        self._occupancy = [0] * n
        self._last_entry = [-math.inf] * n
        self._queues: list[list[_Waiter]] = [[] for _ in range(n)]
        self._open: dict[tuple[str, int], tuple[int, int]] = {}  # (train, seg) -> (enter, dir)
        self._seq = 0

        self.occupancy: list[OccupancyRecord] = []
        self.movements: list[MovementRecord] = []

        self.sim = Simulation(seed=seed)
        self.sim.on(_READY, self._on_ready)
        self.sim.on(_ARRIVE, self._on_arrive)
        self.sim.on(_TRY, self._on_try)
        for train in trains:
            self.sim.schedule_at(float(train.entry_time_s), _READY, train=train.train_id, step=0)

    # -- handlers -----------------------------------------------------------

    def _on_ready(self, sim: Simulation, event: Event) -> None:
        train_id = str(event.payload["train"])
        step = int(event.payload["step"])
        train = self._trains[train_id]
        if step + 1 >= len(train.path):
            return  # already at destination (arrival recorded in _on_arrive)
        self._request(train_id, step, int(sim.now))

    def _on_arrive(self, sim: Simulation, event: Event) -> None:
        train_id = str(event.payload["train"])
        step = int(event.payload["step"])
        train = self._trains[train_id]
        now = int(sim.now)
        self.movements.append(MovementRecord(now, train_id, train.path[step], _ARRIVE))

        seg = _segment_for(train.path[step - 1], train.path[step])
        self._release(seg, train_id, now)
        self._process_queue(seg, now)

        if step + 1 < len(train.path):
            sim.schedule_at(float(now + train.dwell_s), _READY, train=train_id, step=step)

    def _on_try(self, sim: Simulation, event: Event) -> None:
        self._process_queue(int(event.payload["seg"]), int(sim.now))

    # -- resource logic -----------------------------------------------------

    def _request(self, train_id: str, step: int, now: int) -> None:
        train = self._trains[train_id]
        seg = _segment_for(train.path[step], train.path[step + 1])
        if self._can_enter(seg, now):
            self._grant(seg, train_id, step, now)
            return
        # Queue the train (priority desc, then FIFO).
        heapq.heappush(self._queues[seg], _Waiter((-train.priority, self._seq), train_id, step))
        self._seq += 1
        if self._occupancy[seg] < self.corridor.segments[seg].capacity:
            # Blocked only by headway → retry when it elapses.
            self._schedule_try(seg)

    def _can_enter(self, seg: int, now: int) -> bool:
        s = self.corridor.segments[seg]
        return self._occupancy[seg] < s.capacity and now >= self._last_entry[seg] + s.headway_s

    def _grant(self, seg: int, train_id: str, step: int, now: int) -> None:
        train = self._trains[train_id]
        self._occupancy[seg] += 1
        self._last_entry[seg] = now
        direction = _direction(train.path[step], train.path[step + 1])
        self._open[train_id, seg] = (now, direction)
        self.movements.append(MovementRecord(now, train_id, train.path[step], _DEPART))
        arrive_at = now + self.corridor.segments[seg].running_time_s
        self.sim.schedule_at(float(arrive_at), _ARRIVE, train=train_id, step=step + 1)

    def _release(self, seg: int, train_id: str, now: int) -> None:
        self._occupancy[seg] -= 1
        enter, direction = self._open.pop((train_id, seg))
        self.occupancy.append(OccupancyRecord(seg, train_id, direction, enter, now))

    def _process_queue(self, seg: int, now: int) -> None:
        while self._queues[seg] and self._can_enter(seg, now):
            waiter = heapq.heappop(self._queues[seg])
            self._grant(seg, waiter.train_id, waiter.step, now)
        if self._queues[seg] and self._occupancy[seg] < self.corridor.segments[seg].capacity:
            self._schedule_try(seg)

    def _schedule_try(self, seg: int) -> None:
        when = self._last_entry[seg] + self.corridor.segments[seg].headway_s
        self.sim.schedule_at(float(when), _TRY, seg=seg)

    # -- running & analysis -------------------------------------------------

    def run(self) -> RunResult:
        return self.sim.run()

    def max_occupancy(self, segment_index: int) -> int:
        """Maximum number of trains simultaneously on a segment."""
        events: list[tuple[int, int]] = []
        for r in self.occupancy:
            if r.segment_index == segment_index:
                events.append((r.enter_s, 1))
                events.append((r.exit_s, -1))
        events.sort()
        cur = peak = 0
        for _t, delta in events:
            cur += delta
            peak = max(peak, cur)
        return peak

    def overcapacity_segments(self) -> list[int]:
        """Segments where simultaneous occupancy ever exceeded their capacity."""
        return [s.index for s in self.corridor.segments if self.max_occupancy(s.index) > s.capacity]
