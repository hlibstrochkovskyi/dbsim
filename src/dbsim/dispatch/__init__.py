"""Swappable dispatchers for conflict resolution."""

from __future__ import annotations

from dbsim.dispatch.altgraph import (
    AltGraphDispatcher,
    AltGraphProblem,
    AltGraphSolution,
    Operation,
    build_problem_from_meso,
    solve_amcc,
    solve_by_priority,
)
from dbsim.dispatch.base import Dispatcher, SegmentRequest
from dbsim.dispatch.optimal import solve_optimal
from dbsim.dispatch.priority import FifoDispatcher, PriorityDispatcher

#: Dispatchers selectable by name (e.g. on the CLI).
DISPATCHERS: dict[str, type[Dispatcher]] = {
    "priority": PriorityDispatcher,
    "fifo": FifoDispatcher,
}

__all__ = [
    "DISPATCHERS",
    "AltGraphDispatcher",
    "AltGraphProblem",
    "AltGraphSolution",
    "Dispatcher",
    "FifoDispatcher",
    "Operation",
    "PriorityDispatcher",
    "SegmentRequest",
    "build_problem_from_meso",
    "solve_amcc",
    "solve_by_priority",
    "solve_optimal",
]
