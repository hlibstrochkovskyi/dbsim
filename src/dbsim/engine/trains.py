"""Macroscopic train-movement simulation (M1.1).

This is the first domain simulation: it drives every train through its scheduled
stop sequence on top of the deterministic event loop (:mod:`dbsim.engine.loop`).

Movement is *analytical at scheduled-running-time granularity* — no acceleration
curves. Two event types carry a train along its trip:

- ``depart`` (trip, stop *i*): the train leaves stop *i*; we schedule its
  ``arrive`` at stop *i+1* after the scheduled running time.
- ``arrive`` (trip, stop *i*): the train reaches stop *i*; if it is not the last
  stop we schedule its ``depart`` at ``max(scheduled_departure, arrival +
  min_dwell)``.

With **zero perturbation** (``min_dwell_s = 0`` and no injected delay) every
running time and dwell equals the timetable's, so simulated times reproduce the
schedule exactly — the M1.1 acceptance.

The running-time and departure-time rules are isolated in small methods so M1.2
can extend them (primary-delay injection, minimum connection/transfer times,
headway) without touching the loop wiring.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from dbsim.engine.events import Event
from dbsim.engine.loop import RunResult, Simulation
from dbsim.model.timetable import Timetable

_DEPART = "depart"
_ARRIVE = "arrive"


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
    """Drives a set of :class:`TrainSchedule` through the event loop."""

    def __init__(
        self,
        schedules: list[TrainSchedule],
        *,
        seed: int = 0,
        min_dwell_s: int = 0,
    ) -> None:
        self._schedules = {s.trip_id: s for s in schedules}
        self._min_dwell_s = min_dwell_s
        self.records: list[MovementRecord] = []

        self.sim = Simulation(seed=seed)
        self.sim.on(_DEPART, self._on_depart)
        self.sim.on(_ARRIVE, self._on_arrive)

        # Seed each train's first departure (origin stops have no arrival).
        for schedule in schedules:
            first = schedule.stops[0]
            start = first.departure_s if first.departure_s is not None else first.arrival_s
            if start is not None:
                self.sim.schedule_at(float(start), _DEPART, trip=schedule.trip_id, idx=0)

    # -- movement rules (M1.2 extension points) -----------------------------

    def _running_time_s(self, frm: ScheduledStop, to: ScheduledStop) -> int:
        """Scheduled running time between two consecutive stops (>= 0)."""
        depart = frm.departure_s if frm.departure_s is not None else frm.arrival_s
        arrive = to.arrival_s if to.arrival_s is not None else to.departure_s
        if depart is None or arrive is None:
            return 0
        return max(0, arrive - depart)

    def _departure_time_s(self, stop: ScheduledStop, arrived_at_s: int) -> int:
        """When the train may leave a stop: schedule, but never before min dwell."""
        scheduled = stop.departure_s if stop.departure_s is not None else arrived_at_s
        return max(scheduled, arrived_at_s + self._min_dwell_s)

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
        if idx + 1 < len(schedule.stops):
            departure = self._departure_time_s(stop, now)
            sim.schedule_at(float(departure), _DEPART, trip=trip, idx=idx)

    # -- running ------------------------------------------------------------

    def run(self) -> RunResult:
        """Run the simulation to completion, returning the engine result."""
        return self.sim.run()

    def max_abs_deviation_s(self) -> int:
        """Largest absolute deviation of any simulated time from its schedule."""
        return max((abs(d) for r in self.records if (d := r.deviation_s) is not None), default=0)

    def reproduces_schedule(self) -> bool:
        """True if every simulated time equals its scheduled time."""
        return self.max_abs_deviation_s() == 0
