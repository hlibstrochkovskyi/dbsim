"""Mesoscopic segment-occupancy simulation (M2.2) with a swappable dispatcher (M2.4).

The macro engine (M1.1/M1.2) moves trains stop-to-stop without contention. The
**meso** engine adds the track between stations as a *contended resource*: a
segment has a capacity (its number of tracks) and a minimum **headway**, and a
train must acquire the segment before it can run over it. If the segment is full,
within the headway, or **closed** (a disruption), the train **waits at the
station** — so two trains cannot occupy a single-track segment at the same time
(in either direction), and following trains keep their headway.

The *mechanism* (capacity, headway, closures) lives here; the *policy* — which
waiting train is admitted next — is delegated to a swappable
:class:`~dbsim.dispatch.base.Dispatcher` (M2.4), so strategies can be compared.

Conflict *detection/reporting* is M2.3; network-wide deadlock avoidance across
many single-track segments is M3.3 (a single meet does not deadlock).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from dbsim.dispatch.base import Dispatcher, SegmentRequest
from dbsim.dispatch.priority import PriorityDispatcher
from dbsim.engine.events import Event
from dbsim.engine.loop import RunResult, Simulation
from dbsim.model.segments import Segment

_READY = "ready"  # internal: train at a station, ready to take the next segment
_ARRIVE = "arrive"  # train reaches the next station (also the movement-record label)
_TRY = "try"  # internal: re-evaluate a segment's queue (headway elapsed / closure ended)
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
class Closure:
    """A segment closed (unusable) over a time window — a line-closure disruption."""

    segment_index: int
    start_s: int
    end_s: int


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
    event: str  # _DEPART or _ARRIVE


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
    """Runs :class:`MesoTrain`s over a :class:`MesoCorridor` with contention.

    A :class:`~dbsim.dispatch.base.Dispatcher` (default
    :class:`~dbsim.dispatch.priority.PriorityDispatcher`) decides which waiting
    train is admitted to a free segment. ``closures`` block segments over time
    windows; trains hold until the segment reopens.
    """

    def __init__(
        self,
        corridor: MesoCorridor,
        trains: list[MesoTrain],
        *,
        seed: int = 0,
        dispatcher: Dispatcher | None = None,
        closures: list[Closure] | None = None,
    ) -> None:
        self.corridor = corridor
        self.dispatcher = dispatcher if dispatcher is not None else PriorityDispatcher()
        self._trains = {t.train_id: t for t in trains}
        n = len(corridor.segments)
        self._occupancy = [0] * n
        self._last_entry: list[float] = [-math.inf] * n
        # Per-segment waiting trains: train_id -> (request, step). Dict keeps FIFO order.
        self._waiting: list[dict[str, tuple[SegmentRequest, int]]] = [{} for _ in range(n)]
        self._open: dict[tuple[str, int], tuple[int, int]] = {}  # (train, seg) -> (enter, dir)
        self._closures = list(closures or [])

        self.occupancy: list[OccupancyRecord] = []
        self.movements: list[MovementRecord] = []

        self.sim = Simulation(seed=seed)
        self.sim.on(_READY, self._on_ready)
        self.sim.on(_ARRIVE, self._on_arrive)
        self.sim.on(_TRY, self._on_try)
        for train in trains:
            self.sim.schedule_at(float(train.entry_time_s), _READY, train=train.train_id, step=0)
        # Wake each closed segment's queue when it reopens.
        for closure in self._closures:
            self.sim.schedule_at(float(closure.end_s), _TRY, seg=closure.segment_index)

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
        direction = _direction(train.path[step], train.path[step + 1])
        request = SegmentRequest(train_id, train.priority, direction, now)
        self._waiting[seg][train_id] = (request, step)
        self._process_queue(seg, now)

    def _closed(self, seg: int, now: int) -> bool:
        return any(c.segment_index == seg and c.start_s <= now < c.end_s for c in self._closures)

    def _can_enter(self, seg: int, now: int) -> bool:
        s = self.corridor.segments[seg]
        return (
            self._occupancy[seg] < s.capacity
            and now >= self._last_entry[seg] + s.headway_s
            and not self._closed(seg, now)
        )

    def _process_queue(self, seg: int, now: int) -> None:
        while self._waiting[seg] and self._can_enter(seg, now):
            requests = [req for (req, _step) in self._waiting[seg].values()]
            chosen = self.dispatcher.select(self.corridor.segments[seg], requests, now)
            if chosen is None:
                break
            _request, step = self._waiting[seg].pop(chosen)
            self._grant(seg, chosen, step, now)
        # Still waiting but only blocked by headway (not capacity/closure) → retry later.
        if (
            self._waiting[seg]
            and self._occupancy[seg] < self.corridor.segments[seg].capacity
            and not self._closed(seg, now)
        ):
            self._schedule_try(seg)

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

    def _schedule_try(self, seg: int) -> None:
        when = self._last_entry[seg] + self.corridor.segments[seg].headway_s
        self.sim.schedule_at(float(when), _TRY, seg=seg)

    # -- running & analysis -------------------------------------------------

    def run(self) -> RunResult:
        return self.sim.run()

    def completed_trains(self) -> set[str]:
        """Trains that reached their final station."""
        final_of = {tid: t.path[-1] for tid, t in self._trains.items()}
        return {
            m.train_id
            for m in self.movements
            if m.event == _ARRIVE and m.station_index == final_of[m.train_id]
        }

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
