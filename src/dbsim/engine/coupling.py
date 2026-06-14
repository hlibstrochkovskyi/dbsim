"""Macro–micro coupling (M3.4).

The multi-scale architecture made real: a train runs in the **macro** model
(national timetable) until it reaches the boundary of the curated **micro** zone,
is **handed off** to the block-level micro simulation (which runs it through the
passing loop, resolving meets and avoiding deadlock, M3.3), and is **handed back**
to macro on the far side. The micro-level meet resolution therefore propagates
into macro timing — a question neither a pure-macro nor a single-zone-micro model
can answer.

The coupling is a clean boundary contract:

- **macro → micro:** a :class:`BoundaryArrival` carries the macro time at which a
  train reaches its *entry* boundary; that is when the micro run starts.
- **micro → macro:** the micro **exit** time at the far boundary is when macro
  resumes.

A hand-off is **time-consistent** iff the micro entry is not before the macro
arrival (no time travel — a train may wait at the boundary if the zone is
occupied, but never enter early) and the exit is after the entry. The coupled run
is deterministic because both layers are.
"""

from __future__ import annotations

from dataclasses import dataclass

from dbsim.engine.micro_meet import MicroMeetSimulation, MicroMeetTrain
from dbsim.model.micro import MicroZone


@dataclass(frozen=True, slots=True)
class BoundaryArrival:
    """Macro → micro: a train reaching the zone's entry boundary."""

    train_id: str
    direction: str  # "WE" | "EW"
    macro_arrival_s: int


@dataclass(frozen=True, slots=True)
class HandOff:
    """A train's passage through the zone: macro in, micro through, macro out."""

    train_id: str
    entry_boundary: str
    entry_time_s: int  # when the train actually entered the zone (micro)
    exit_boundary: str
    exit_time_s: int  # when macro resumes on the far side
    macro_arrival_s: int  # when it reached the entry boundary (macro)
    met: bool  # did it wait for an opposing train in the zone?

    @property
    def zone_time_s(self) -> int:
        return self.exit_time_s - self.macro_arrival_s

    @property
    def boundary_wait_s(self) -> int:
        """Time held at the entry boundary before the zone let it in."""
        return self.entry_time_s - self.macro_arrival_s


@dataclass(frozen=True, slots=True)
class CoupledResult:
    """Outcome of a coupled macro–micro run over the zone."""

    handoffs: tuple[HandOff, ...]
    deadlocked: bool
    consistent: bool


def couple_zone(
    zone: MicroZone,
    arrivals: list[BoundaryArrival],
    *,
    avoid: bool = True,
    seed: int = 0,
) -> CoupledResult:
    """Run the boundary hand-offs: macro arrivals → micro zone → macro resume."""
    trains = [
        MicroMeetTrain(a.train_id, a.direction, entry_time_s=a.macro_arrival_s) for a in arrivals
    ]
    micro = MicroMeetSimulation(zone, trains, avoid=avoid, seed=seed).run()

    handoffs: list[HandOff] = []
    consistent = not micro.deadlocked
    for a in arrivals:
        events = [e for e in micro.events if e.train_id == a.train_id]
        enters = [e.time_s for e in events if e.action == "enter"]
        exits = [e.time_s for e in events if e.action == "exit"]
        if not enters or not exits:
            consistent = False  # train did not complete (deadlock)
            continue
        entry_time = enters[0]
        exit_time = exits[0]
        entry_boundary = zone.west_boundary if a.direction == "WE" else zone.east_boundary
        exit_boundary = zone.east_boundary if a.direction == "WE" else zone.west_boundary
        if entry_time < a.macro_arrival_s or exit_time <= entry_time:
            consistent = False  # acausal hand-off
        handoffs.append(
            HandOff(
                train_id=a.train_id,
                entry_boundary=entry_boundary,
                entry_time_s=entry_time,
                exit_boundary=exit_boundary,
                exit_time_s=exit_time,
                macro_arrival_s=a.macro_arrival_s,
                met=any(e.action == "wait" for e in events),
            )
        )
    return CoupledResult(tuple(handoffs), micro.deadlocked, consistent)
