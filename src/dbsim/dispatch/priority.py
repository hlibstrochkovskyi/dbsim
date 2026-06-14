"""Simple rule-based dispatchers (M2.4 v1)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from dbsim.dispatch.base import Dispatcher, SegmentRequest

if TYPE_CHECKING:
    from dbsim.engine.meso import MesoSegment


class PriorityDispatcher(Dispatcher):
    """Admit the highest-priority waiting train, breaking ties by who waited longest."""

    name = "priority"

    def select(
        self, segment: MesoSegment, waiting: Sequence[SegmentRequest], now: int
    ) -> str | None:
        if not waiting:
            return None
        best = min(waiting, key=lambda r: (-r.priority, r.requested_at_s, r.train_id))
        return best.train_id


class FifoDispatcher(Dispatcher):
    """Admit whoever has waited longest, ignoring priority (first come, first served)."""

    name = "fifo"

    def select(
        self, segment: MesoSegment, waiting: Sequence[SegmentRequest], now: int
    ) -> str | None:
        if not waiting:
            return None
        best = min(waiting, key=lambda r: (r.requested_at_s, r.train_id))
        return best.train_id
