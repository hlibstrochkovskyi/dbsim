"""Microscopic movement & blocking-time model (M3.2).

This is the finest-grain dynamics: a train runs over the curated micro zone's
**blocks** (M3.1) with a real speed profile — it accelerates, honours each block's
speed limit, and brakes for slower blocks ahead — and each block is reserved for a
**blocking time** longer than the running time.

Blocking-time theory (Pachl): a block is not free for the next train merely while
this train is on it. It is reserved from when the train passes the *approach*
signal (one block back, so it can stop if the block is occupied) until the train's
rear has fully *cleared* the block and the route is released. The blocking time is

    [ approach-signal passed − setup ,  rear cleared + release ]

Plotting each block's blocking interval against distance over time gives the
**blocking-time stairway**; the minimum line headway is the time offset at which a
following train's stairway just touches the leader's.

The speed profile is computed by the standard forward/backward passes over a fine
distance grid, so it is deterministic (a determinism test ships with this module).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from dbsim.model.micro import MicroRoute, MicroZone

_KMH = 1.0 / 3.6  # km/h → m/s


@dataclass(frozen=True, slots=True)
class TrainDynamics:
    """A train's longitudinal dynamics (mass-independent kinematics)."""

    accel_ms2: float = 0.8
    decel_ms2: float = 0.6
    max_speed_kmh: int = 120
    length_m: float = 75.0


@dataclass(frozen=True, slots=True)
class BlockTraversal:
    """When and how fast a train's front passes through one block."""

    block_id: str
    dist_start_m: float
    dist_end_m: float
    enter_s: float
    exit_s: float
    enter_speed_ms: float
    exit_speed_ms: float

    @property
    def running_time_s(self) -> float:
        return self.exit_s - self.enter_s


@dataclass(frozen=True, slots=True)
class BlockingInterval:
    """A block's blocking time over a distance range (one stair of the stairway)."""

    block_id: str
    dist_start_m: float
    dist_end_m: float
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _route_block_limits(
    zone: MicroZone, route: MicroRoute, dyn: TrainDynamics
) -> list[tuple[str, float, float]]:
    """Per route block: (id, length_m, speed_limit_ms) capped by the train."""
    cap = dyn.max_speed_kmh
    out: list[tuple[str, float, float]] = []
    for block_id in route.blocks:
        b = zone.block(block_id)
        out.append((b.id, b.length_m, min(b.max_speed_kmh, cap) * _KMH))
    return out


def micro_trajectory(
    zone: MicroZone,
    route: MicroRoute,
    dyn: TrainDynamics,
    *,
    start_time_s: float = 0.0,
    entry_speed_ms: float = 0.0,
    exit_speed_ms: float = 0.0,
    dx_m: float = 5.0,
) -> list[BlockTraversal]:
    """Compute the per-block trajectory of a train over a route.

    A trapezoidal speed profile is built on a fine grid via a forward
    (acceleration) pass and a backward (braking) pass that respect every block's
    speed limit and the requested entry/exit speeds.
    """
    blocks = _route_block_limits(zone, route, dyn)
    # Build the distance grid; remember each block's [start_node, end_node] indices.
    xs: list[float] = [0.0]
    vlim: list[float] = []
    bounds: list[tuple[int, int]] = []
    x = 0.0
    for _bid, length, limit in blocks:
        start_node = len(xs) - 1
        steps = max(1, math.ceil(length / dx_m))
        step_len = length / steps
        for _ in range(steps):
            x += step_len
            xs.append(x)
            vlim.append(limit)  # limit on the segment ending at this node
        bounds.append((start_node, len(xs) - 1))
    n = len(xs) - 1

    # Speed at each node. Segment k connects node k → k+1 with limit vlim[k].
    v = [0.0] * (n + 1)
    v[0] = min(entry_speed_ms, vlim[0])
    for k in range(n):  # forward: acceleration
        reach = math.sqrt(v[k] ** 2 + 2 * dyn.accel_ms2 * (xs[k + 1] - xs[k]))
        v[k + 1] = min(vlim[k], reach)
    # Constrain the exit speed (0 ⇒ brake to a stop; a large value ⇒ run through).
    v[n] = min(v[n], exit_speed_ms)
    for k in range(n - 1, -1, -1):  # backward: braking
        brakeable = math.sqrt(v[k + 1] ** 2 + 2 * dyn.decel_ms2 * (xs[k + 1] - xs[k]))
        v[k] = min(v[k], brakeable)

    # Integrate time along the grid.
    t = [0.0] * (n + 1)
    t[0] = start_time_s
    for k in range(n):
        avg = (v[k] + v[k + 1]) / 2
        dt = (xs[k + 1] - xs[k]) / avg if avg > 1e-6 else 0.0
        t[k + 1] = t[k] + dt

    return [
        BlockTraversal(
            block_id=blocks[i][0],
            dist_start_m=xs[s],
            dist_end_m=xs[e],
            enter_s=t[s],
            exit_s=t[e],
            enter_speed_ms=v[s],
            exit_speed_ms=v[e],
        )
        for i, (s, e) in enumerate(bounds)
    ]


def blocking_times(
    traversals: list[BlockTraversal],
    dyn: TrainDynamics,
    *,
    setup_s: float = 8.0,
    release_s: float = 4.0,
) -> list[BlockingInterval]:
    """Blocking-time interval per block (approach-reserved → cleared + released)."""
    intervals: list[BlockingInterval] = []
    for i, tr in enumerate(traversals):
        # Reserved from when the train passes the approach signal — the entry of
        # the previous block — minus the route-setup time.
        approach_enter = traversals[i - 1].enter_s if i > 0 else tr.enter_s
        start = approach_enter - setup_s
        clearing = dyn.length_m / max(tr.exit_speed_ms, 1.0)
        end = tr.exit_s + clearing + release_s
        intervals.append(BlockingInterval(tr.block_id, tr.dist_start_m, tr.dist_end_m, start, end))
    return intervals
