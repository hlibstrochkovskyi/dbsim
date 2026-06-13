"""The priority-queue event loop.

Design notes (these encode the project's guiding principles):

- **Deterministic.** The heap stores ``(time, sequence, event)`` tuples. The
  monotonically increasing ``sequence`` is the *sole* tie-breaker for equal
  times, giving stable FIFO ordering and ensuring two ``Event`` objects are
  never compared directly. Same inputs + same seed → identical event order.
- **No acausal effects.** Scheduling an event in the past (``time < now``) is a
  programming error and raises. Nothing may propagate backward in time — a
  property M1.2's delay model will rely on.
- **Randomness flows through one RNG.** The loop owns a single seeded
  :class:`random.Random` (see :mod:`dbsim.seed`); handlers must use ``sim.rng``
  rather than the global ``random`` module.
"""

from __future__ import annotations

import heapq
import random
from collections.abc import Callable
from dataclasses import dataclass

from dbsim.engine.events import Event
from dbsim.seed import DEFAULT_SEED, make_rng

#: A handler receives the running simulation and the event being processed. It
#: may schedule further events via ``sim.schedule(...)``.
Handler = Callable[["Simulation", Event], None]


@dataclass(frozen=True, slots=True)
class RunResult:
    """The immutable outcome of a finished simulation run.

    Attributes:
        events: The events processed, in the exact order they fired.
        end_time: The simulation time of the last processed event (``0.0`` if no
            events fired).
        seed: The seed the run used, recorded for reproducibility.
    """

    events: tuple[Event, ...]
    end_time: float
    seed: int


class Simulation:
    """A headless, deterministic, event-driven simulation.

    Register handlers with :meth:`on`, seed the world, schedule initial events,
    then call :meth:`run`. The loop processes events in non-decreasing time
    order until the queue drains or ``max_time`` is exceeded.
    """

    def __init__(self, *, seed: int = DEFAULT_SEED, max_time: float | None = None) -> None:
        if max_time is not None and max_time < 0:
            raise ValueError(f"max_time must be non-negative, got {max_time}")
        self._seed = seed
        self._max_time = max_time
        self._heap: list[tuple[float, int, Event]] = []
        self._sequence = 0
        self._now = 0.0
        self._handlers: dict[str, Handler] = {}
        self._log: list[Event] = []
        #: The single seeded RNG; all stochasticity must flow through this.
        self.rng: random.Random = make_rng(seed)

    @property
    def now(self) -> float:
        """The current simulation time (time of the event being processed)."""
        return self._now

    @property
    def seed(self) -> int:
        """The seed this simulation was constructed with."""
        return self._seed

    def on(self, kind: str, handler: Handler) -> None:
        """Register the handler invoked when an event of ``kind`` is processed.

        Re-registering a ``kind`` replaces the previous handler.
        """
        self._handlers[kind] = handler

    def schedule(self, event: Event) -> None:
        """Add an event to the queue.

        Raises:
            ValueError: if ``event.time`` is before the current time (acausal).
        """
        if event.time < self._now:
            raise ValueError(
                f"acausal schedule: event at t={event.time} is before now t={self._now}"
            )
        heapq.heappush(self._heap, (event.time, self._sequence, event))
        self._sequence += 1

    def schedule_at(self, time: float, kind: str, **payload: object) -> None:
        """Convenience wrapper: build and :meth:`schedule` an :class:`Event`."""
        self.schedule(Event(time=time, kind=kind, payload=dict(payload)))

    def run(self) -> RunResult:
        """Process events until the queue is empty or ``max_time`` is passed.

        Returns a :class:`RunResult` recording every processed event in order.
        """
        while self._heap:
            time, _, event = heapq.heappop(self._heap)
            if self._max_time is not None and time > self._max_time:
                break
            self._now = time
            self._log.append(event)
            handler = self._handlers.get(event.kind)
            if handler is not None:
                handler(self, event)
        return RunResult(events=tuple(self._log), end_time=self._now, seed=self._seed)
