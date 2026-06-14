"""Micro-validation harness (M3.5).

The micro layer must be *checked against reality*, not trusted — micro errors
compound fast. GTFS-RT covers this regional line too sparsely for an observed-
delay study, so the validation uses the **operated GTFS timetable** as ground
truth: on a single-track line trains can only meet at loops, so the real schedule
is a strong test of the loop model.

It runs the day's trains through the coupled micro zone (M3.4) and checks:

- **meet structure** — the timetable's opposing-train meets at the loop are all
  resolved without deadlock (the modelled infrastructure supports the operation);
- **occupancy** — the loop is never occupied beyond its track count;
- **capacity headroom** — the micro minimum headway is well below the operated
  train spacing (the timetable is feasible at micro fidelity).

The report states the residual gap (utilisation vs slack) for discussion.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from dbsim.analysis.stairway import minimum_headway_s
from dbsim.engine.blocking import TrainDynamics, blocking_times, micro_trajectory
from dbsim.engine.coupling import BoundaryArrival, couple_zone
from dbsim.model.micro import MicroZone


@dataclass(frozen=True, slots=True)
class MicroValidationReport:
    """How well the micro zone reproduces the operated timetable."""

    service_date: int
    n_trains: int
    n_meets: int
    max_occupancy: int
    capacity: int
    micro_min_headway_s: int
    min_observed_headway_s: int
    consistent: bool
    deadlocked: bool

    @property
    def occupancy_ok(self) -> bool:
        return self.max_occupancy <= self.capacity

    @property
    def utilisation(self) -> float:
        """Micro capacity used by the operated spacing (headway / observed spacing)."""
        if self.min_observed_headway_s <= 0:
            return 0.0
        return self.micro_min_headway_s / self.min_observed_headway_s

    @property
    def passes(self) -> bool:
        """The zone model is consistent with the operated timetable."""
        return self.consistent and not self.deadlocked and self.occupancy_ok


def micro_min_headway_s(
    zone: MicroZone, dynamics: TrainDynamics, *, route_name: str = "WE_t1"
) -> int:
    """The micro minimum headway on a through route (blocking-time critical block)."""
    route = next(r for r in zone.routes if r.name == route_name)
    traj = micro_trajectory(zone, route, dynamics, entry_speed_ms=999, exit_speed_ms=999)
    blocking = blocking_times(traj, dynamics)
    return round(minimum_headway_s(blocking, blocking))


def validate_micro_zone(
    zone: MicroZone,
    arrivals: list[BoundaryArrival],
    service_date: int,
    *,
    dynamics: TrainDynamics | None = None,
) -> MicroValidationReport:
    """Run the day's trains through the zone and score it against the timetable."""
    dyn = dynamics or TrainDynamics()
    result = couple_zone(zone, arrivals)

    # Sweep the zone occupation windows: peak occupancy + opposing-pair meets.
    events: list[tuple[int, int, str]] = []
    for h in result.handoffs:
        events.append((h.entry_time_s, 1, h.entry_boundary))
        events.append((h.exit_time_s, -1, h.entry_boundary))
    events.sort(key=lambda e: (e[0], e[1]))
    active_boundaries: list[str] = []
    peak = meets = 0
    for _t, delta, boundary in events:
        if delta == 1:
            meets += sum(1 for b in active_boundaries if b != boundary)
            active_boundaries.append(boundary)
            peak = max(peak, len(active_boundaries))
        else:
            active_boundaries.remove(boundary)

    # Tightest spacing between consecutive same-direction zone entries.
    by_dir: dict[str, list[int]] = {}
    for h in result.handoffs:
        by_dir.setdefault(h.entry_boundary, []).append(h.entry_time_s)
    gaps = [b - a for times in by_dir.values() for a, b in pairwise(sorted(times))]

    return MicroValidationReport(
        service_date=service_date,
        n_trains=len(result.handoffs),
        n_meets=meets,
        max_occupancy=peak,
        capacity=len(zone.loop_blocks),
        micro_min_headway_s=micro_min_headway_s(zone, dyn),
        min_observed_headway_s=min(gaps) if gaps else 0,
        consistent=result.consistent,
        deadlocked=result.deadlocked,
    )
