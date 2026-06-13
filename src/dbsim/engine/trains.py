"""Macroscopic train-movement simulation with delay propagation (M1.1 + M1.2).

M1.1 drives trains through their scheduled stops on the deterministic event loop.
M1.2 adds delay propagation — the project's leading research question:

- **Primary-delay injection** — hold a train's departure at a given stop by *N*
  seconds (e.g. "ICE X +20 min at Frankfurt").
- **Dwell-time constraints with recovery** — a train may not leave a stop before
  ``arrival + min_dwell``; where the timetable has dwell slack it recovers part
  of an incoming delay.
- **Minimum connection/transfer holding** — a declared connection holds a
  connecting train's departure until ``feeder_arrival + min_transfer``, but no
  later than ``scheduled_departure + max_wait`` (after which the connection is
  dropped). This is the cross-train propagation.

Two invariants hold by construction:

- **No acausal effects.** Every scheduled time is ``>= now`` (the loop rejects
  the past) and delays are only ever *added*, so a simulated time is never
  earlier than its schedule and a delay never propagates to an earlier stop.
- **Deterministic.** Same inputs + seed → identical run.

Macro-level *headway* (track contention between trains) is intentionally **not**
here: it needs the segment/occupancy model and is built in Phase 2 (M2.2).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from dbsim.engine.events import Event
from dbsim.engine.loop import RunResult, Simulation
from dbsim.model.timetable import Timetable

_DEPART = "depart"
_ARRIVE = "arrive"
_CONN_DEADLINE = "conn_deadline"


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    """A train's scheduled call at one stop."""

    seq: int
    stop_id: str
    arrival_s: int | None
    departure_s: int | None


@dataclass(frozen=True, slots=True)
class TrainSchedule:
    """A single train's ordered scheduled stops."""

    trip_id: str
    line: str | None
    stops: tuple[ScheduledStop, ...]


@dataclass(frozen=True, slots=True)
class PrimaryDelay:
    """A primary delay: hold ``trip_id`` at stop ``seq`` by ``delay_s`` seconds."""

    trip_id: str
    seq: int
    delay_s: int


@dataclass(frozen=True, slots=True)
class Connection:
    """A protected transfer: ``connector_trip`` waits for ``feeder_trip``.

    The connector's departure at its ``connector_seq`` stop is held until
    ``feeder_arrival + min_transfer_s``, but never beyond
    ``scheduled_departure + max_wait_s`` (past which the connection is dropped).
    """

    feeder_trip: str
    feeder_stop_id: str
    connector_trip: str
    connector_seq: int
    min_transfer_s: int = 300
    max_wait_s: int = 600


@dataclass(frozen=True, slots=True)
class MovementRecord:
    """One simulated movement event: a train arriving at / departing a stop."""

    time_s: int
    trip_id: str
    seq: int
    stop_id: str
    event: str  # _ARRIVE or _DEPART
    scheduled_s: int | None

    @property
    def deviation_s(self) -> int | None:
        """Simulated minus scheduled time, or ``None`` if no scheduled time."""
        return None if self.scheduled_s is None else self.time_s - self.scheduled_s


@dataclass(slots=True)
class _Waiting:
    """Internal state for a connector held at a connection stop."""

    trip_id: str
    idx: int
    base_t: int  # the connector's own earliest departure, absent any feeder
    required_t: int  # raised above base_t only when a feeder is actually caught
    deadline: int  # sched_dep + max_wait — the backstop give-up time
    pending: set[tuple[str, str]]  # feeder keys still awaited
    resolved: bool = False


def load_schedules(
    tt: Timetable, day: date | int | str, trip_ids: set[str] | None = None
) -> list[TrainSchedule]:
    """Load the scheduled stop sequences of the trains active on ``day``.

    Args:
        tt: The timetable read model.
        day: Service date.
        trip_ids: If given, restrict to these trips; otherwise all active trains.
    """
    active = tt.services_on(day)
    if not active:
        return []

    sql = """
        SELECT t.trip_id, r.route_short_name, st.stop_sequence,
               st.stop_id, st.arrival_s, st.departure_s
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        LEFT JOIN routes r ON t.route_id = r.route_id
        WHERE t.service_id IN (SELECT UNNEST(?))
    """
    params: list[object] = [list(active)]
    if trip_ids is not None:
        if not trip_ids:
            return []
        sql += " AND t.trip_id IN (SELECT UNNEST(?))"
        params.append(list(trip_ids))
    sql += " ORDER BY t.trip_id, st.stop_sequence"

    rows = tt.connection.execute(sql, params).fetchall()

    by_trip: dict[str, list[ScheduledStop]] = defaultdict(list)
    line_of: dict[str, str | None] = {}
    for trip_id, line, seq, stop_id, arr_s, dep_s in rows:
        line_of.setdefault(trip_id, line)
        by_trip[trip_id].append(ScheduledStop(int(seq), str(stop_id), arr_s, dep_s))

    return [
        TrainSchedule(trip_id, line_of[trip_id], tuple(stops)) for trip_id, stops in by_trip.items()
    ]


class MacroSimulation:
    """Drives :class:`TrainSchedule`s through the event loop, with delay propagation."""

    def __init__(
        self,
        schedules: list[TrainSchedule],
        *,
        seed: int = 0,
        min_dwell_s: int = 0,
        primary_delays: Iterable[PrimaryDelay] | None = None,
        connections: Iterable[Connection] | None = None,
    ) -> None:
        self._schedules = {s.trip_id: s for s in schedules}
        self._min_dwell_s = min_dwell_s
        self.records: list[MovementRecord] = []

        # Primary delays indexed by (trip_id, stop seq).
        self._primary: dict[tuple[str, int], int] = {}
        for pd in primary_delays or ():
            self._primary[pd.trip_id, pd.seq] = (
                self._primary.get((pd.trip_id, pd.seq), 0) + pd.delay_s
            )

        # Connections indexed by the connector's (trip_id, seq).
        self._conns_by_connector: dict[tuple[str, int], list[Connection]] = defaultdict(list)
        self._waiters_by_feeder: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
        for conn in connections or ():
            self._conns_by_connector[conn.connector_trip, conn.connector_seq].append(conn)

        # Runtime state for connection holding.
        self._feeder_arrivals: dict[tuple[str, str], int] = {}
        self._waiting: dict[tuple[str, int], _Waiting] = {}

        self.sim = Simulation(seed=seed)
        self.sim.on(_DEPART, self._on_depart)
        self.sim.on(_ARRIVE, self._on_arrive)
        self.sim.on(_CONN_DEADLINE, self._on_conn_deadline)

        # Seed each train's first departure (origin stops have no arrival).
        for schedule in schedules:
            first = schedule.stops[0]
            sched = first.departure_s if first.departure_s is not None else first.arrival_s
            if sched is not None:
                start = sched + self._primary.get((schedule.trip_id, first.seq), 0)
                self.sim.schedule_at(float(start), _DEPART, trip=schedule.trip_id, idx=0)

    # -- movement rules (extension points) ----------------------------------

    def _running_time_s(self, frm: ScheduledStop, to: ScheduledStop) -> int:
        """Scheduled running time between two consecutive stops (>= 0)."""
        depart = frm.departure_s if frm.departure_s is not None else frm.arrival_s
        arrive = to.arrival_s if to.arrival_s is not None else to.departure_s
        if depart is None or arrive is None:
            return 0
        return max(0, arrive - depart)

    # -- handlers -----------------------------------------------------------

    def _on_depart(self, sim: Simulation, event: Event) -> None:
        trip = str(event.payload["trip"])
        idx = int(event.payload["idx"])
        schedule = self._schedules[trip]
        stop = schedule.stops[idx]
        now = int(sim.now)
        self.records.append(
            MovementRecord(now, trip, stop.seq, stop.stop_id, _DEPART, stop.departure_s)
        )
        nxt = idx + 1
        if nxt < len(schedule.stops):
            running = self._running_time_s(stop, schedule.stops[nxt])
            sim.schedule_at(float(now + running), _ARRIVE, trip=trip, idx=nxt)

    def _on_arrive(self, sim: Simulation, event: Event) -> None:
        trip = str(event.payload["trip"])
        idx = int(event.payload["idx"])
        schedule = self._schedules[trip]
        stop = schedule.stops[idx]
        now = int(sim.now)
        self.records.append(
            MovementRecord(now, trip, stop.seq, stop.stop_id, _ARRIVE, stop.arrival_s)
        )

        # This arrival may release connectors waiting on this feeder.
        feeder_key = (trip, stop.stop_id)
        self._feeder_arrivals[feeder_key] = now
        self._release_waiters(sim, feeder_key, now)

        # Decide this train's own onward departure.
        if idx + 1 < len(schedule.stops):
            self._decide_departure(sim, trip, idx, now)

    def _decide_departure(self, sim: Simulation, trip: str, idx: int, arrived_at: int) -> None:
        schedule = self._schedules[trip]
        stop = schedule.stops[idx]
        scheduled = stop.departure_s if stop.departure_s is not None else arrived_at
        base_t = max(scheduled, arrived_at + self._min_dwell_s) + self._primary.get(
            (trip, stop.seq), 0
        )

        conns = self._conns_by_connector.get((trip, stop.seq))
        if not conns:
            sim.schedule_at(float(base_t), _DEPART, trip=trip, idx=idx)
            return

        required_t = base_t
        pending: set[tuple[str, str]] = set()
        deadline = base_t
        for conn in conns:
            conn_deadline = scheduled + conn.max_wait_s
            deadline = max(deadline, conn_deadline)
            fkey = (conn.feeder_trip, conn.feeder_stop_id)
            arrived = self._feeder_arrivals.get(fkey)
            if arrived is not None:
                hold = arrived + conn.min_transfer_s
                if hold <= conn_deadline:
                    required_t = max(required_t, hold)
            elif conn_deadline > base_t:
                pending.add(fkey)

        if not pending:
            sim.schedule_at(float(required_t), _DEPART, trip=trip, idx=idx)
            return

        waiting = _Waiting(trip, idx, base_t, required_t, deadline, pending)
        self._waiting[trip, idx] = waiting
        for fkey in pending:
            self._waiters_by_feeder[fkey].append((trip, idx))
        sim.schedule_at(float(deadline), _CONN_DEADLINE, trip=trip, idx=idx)

    def _release_waiters(self, sim: Simulation, fkey: tuple[str, str], now: int) -> None:
        waiters = self._waiters_by_feeder.get(fkey)
        if not waiters:
            return
        for key in waiters:
            waiting = self._waiting.get(key)
            if waiting is None or waiting.resolved or fkey not in waiting.pending:
                continue
            for conn in self._conns_by_connector[key]:
                if (conn.feeder_trip, conn.feeder_stop_id) != fkey:
                    continue
                scheduled = self._scheduled_departure(key)
                hold = now + conn.min_transfer_s
                if hold <= scheduled + conn.max_wait_s:
                    waiting.required_t = max(waiting.required_t, hold)
                # else: this feeder is too late to catch — dropped, not held for.
            waiting.pending.discard(fkey)
            # Depart now only if a feeder was actually caught; otherwise keep
            # waiting until the max_wait deadline (a no-show must not shorten it).
            if not waiting.pending and waiting.required_t > waiting.base_t:
                self._finalize_departure(sim, waiting, now)

    def _on_conn_deadline(self, sim: Simulation, event: Event) -> None:
        key = (str(event.payload["trip"]), int(event.payload["idx"]))
        waiting = self._waiting.get(key)
        if waiting is None or waiting.resolved:
            return
        self._finalize_departure(sim, waiting, int(sim.now))

    def _finalize_departure(self, sim: Simulation, waiting: _Waiting, now: int) -> None:
        waiting.resolved = True
        sim.schedule_at(
            float(max(waiting.required_t, now)), _DEPART, trip=waiting.trip_id, idx=waiting.idx
        )

    def _scheduled_departure(self, key: tuple[str, int]) -> int:
        trip, idx = key
        stop = self._schedules[trip].stops[idx]
        return stop.departure_s if stop.departure_s is not None else (stop.arrival_s or 0)

    # -- running ------------------------------------------------------------

    def run(self) -> RunResult:
        """Run the simulation to completion, returning the engine result."""
        return self.sim.run()

    # -- metrics ------------------------------------------------------------

    def max_abs_deviation_s(self) -> int:
        """Largest absolute deviation of any simulated time from its schedule."""
        return max((abs(d) for r in self.records if (d := r.deviation_s) is not None), default=0)

    def reproduces_schedule(self) -> bool:
        """True if every simulated time equals its scheduled time."""
        return self.max_abs_deviation_s() == 0

    def delayed_event_count(self) -> int:
        """Number of movement events that occurred later than scheduled."""
        return sum(1 for r in self.records if (r.deviation_s or 0) > 0)

    def total_delay_s(self) -> int:
        """Sum of positive deviations across all events."""
        return sum(d for r in self.records if (d := r.deviation_s or 0) > 0)

    def worst_trains(self, n: int = 5) -> list[tuple[str, int]]:
        """The ``n`` trains with the largest maximum delay, descending."""
        by_trip: dict[str, int] = defaultdict(int)
        for r in self.records:
            dev = r.deviation_s or 0
            if dev > by_trip[r.trip_id]:
                by_trip[r.trip_id] = dev
        ranked = sorted(by_trip.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(trip, delay) for trip, delay in ranked[:n] if delay > 0]
