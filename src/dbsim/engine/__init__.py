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

from dbsim.engine.blocking import (
    BlockingInterval,
    BlockTraversal,
    TrainDynamics,
    blocking_times,
    micro_trajectory,
)
from dbsim.engine.coupling import BoundaryArrival, CoupledResult, HandOff, couple_zone
from dbsim.engine.events import Event
from dbsim.engine.loop import RunResult, Simulation
from dbsim.engine.meso import (
    Closure,
    MesoCorridor,
    MesoSegment,
    MesoSimulation,
    MesoTrain,
    OccupancyRecord,
    meso_corridor_from_segments,
)
from dbsim.engine.micro_meet import (
    MeetEvent,
    MeetResult,
    MicroMeetSimulation,
    MicroMeetTrain,
)
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
    "BlockTraversal",
    "BlockingInterval",
    "BoundaryArrival",
    "Closure",
    "Connection",
    "CoupledResult",
    "Event",
    "HandOff",
    "MacroSimulation",
    "MeetEvent",
    "MeetResult",
    "MesoCorridor",
    "MesoSegment",
    "MesoSimulation",
    "MesoTrain",
    "MicroMeetSimulation",
    "MicroMeetTrain",
    "MovementRecord",
    "OccupancyRecord",
    "PrimaryDelay",
    "RunResult",
    "ScheduledStop",
    "Simulation",
    "TrainDynamics",
    "TrainSchedule",
    "blocking_times",
    "couple_zone",
    "load_schedules",
    "meso_corridor_from_segments",
    "micro_trajectory",
]
