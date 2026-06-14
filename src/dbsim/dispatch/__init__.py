"""Swappable dispatchers for conflict resolution."""

from __future__ import annotations

from dbsim.dispatch.base import Dispatcher, SegmentRequest
from dbsim.dispatch.priority import FifoDispatcher, PriorityDispatcher

#: Dispatchers selectable by name (e.g. on the CLI).
DISPATCHERS: dict[str, type[Dispatcher]] = {
    "priority": PriorityDispatcher,
    "fifo": FifoDispatcher,
}

__all__ = [
    "DISPATCHERS",
    "Dispatcher",
    "FifoDispatcher",
    "PriorityDispatcher",
    "SegmentRequest",
]
