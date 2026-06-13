"""The event-driven simulation core.

A railway follows a timetable, so the engine is *event-driven*, not tick-based:
it jumps between discrete events (depart, arrive, …) held in a priority queue,
rather than advancing a fixed clock. Interpolation between events is a concern
for visualization, not the core.

Public surface:

- :class:`~dbsim.engine.events.Event` — an immutable scheduled occurrence.
- :class:`~dbsim.engine.loop.Simulation` — the priority-queue event loop.
- :class:`~dbsim.engine.loop.RunResult` — the immutable record of a finished run.
"""

from __future__ import annotations

from dbsim.engine.events import Event
from dbsim.engine.loop import RunResult, Simulation
from dbsim.engine.trains import (
    Connection,
    MacroSimulation,
    MovementRecord,
    PrimaryDelay,
    ScheduledStop,
    TrainSchedule,
    load_schedules,
)

__all__ = [
    "Connection",
    "Event",
    "MacroSimulation",
    "MovementRecord",
    "PrimaryDelay",
    "RunResult",
    "ScheduledStop",
    "Simulation",
    "TrainSchedule",
    "load_schedules",
]
