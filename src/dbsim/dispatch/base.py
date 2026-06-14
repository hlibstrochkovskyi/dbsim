"""The swappable dispatcher interface (M2.4).

A **dispatcher** decides which of the trains *waiting* for a contended segment is
admitted next. It is a pluggable strategy — comparing dispatching strategies is
one of this tool's main research outputs — so the meso engine owns the mechanism
(capacity, headway, closures) while the dispatcher owns the *policy*.

The interface is deliberately tiny: given a segment and the trains currently
waiting for it, return the ``train_id`` to admit, or ``None`` to admit none right
now. The engine calls it whenever a slot on the segment is free.

Later strategies (the alternative-graph dispatcher in M4.1, a MILP/CP baseline in
M4.2) implement the same interface and can be compared head-to-head.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbsim.engine.meso import MesoSegment


@dataclass(frozen=True, slots=True)
class SegmentRequest:
    """A train waiting to enter a segment."""

    train_id: str
    priority: int
    direction: int  # +1 / -1
    requested_at_s: int


class Dispatcher(ABC):
    """Strategy that orders waiting trains onto a contended segment."""

    #: Short identifier used in CLI/reports.
    name: str = "dispatcher"

    @abstractmethod
    def select(
        self, segment: MesoSegment, waiting: Sequence[SegmentRequest], now: int
    ) -> str | None:
        """Return the ``train_id`` to admit next, or ``None`` to admit none now."""
