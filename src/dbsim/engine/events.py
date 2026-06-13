"""Event definitions for the simulation core."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable occurrence scheduled to be processed at a point in sim time.

    Attributes:
        time: The simulation time at which the event fires. Unit is seconds since
            the start of the simulated day (a convention the higher layers adopt;
            the engine itself only requires a monotonic, non-negative float).
        kind: A short string tag (e.g. ``"depart"``, ``"arrive"``, ``"tick"``)
            used to dispatch the event to its registered handler.
        payload: Arbitrary, JSON-serialisable event data. **Never** used for
            ordering — equal-time events are ordered by insertion sequence inside
            the loop — so two events with identical ``time`` and ``kind`` but
            different payloads remain well-ordered and deterministic.

    The event is frozen: once scheduled it must not change. Treat ``payload`` as
    read-only by convention (it is a plain mapping for ergonomics, not deep
    immutability).
    """

    time: float
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
