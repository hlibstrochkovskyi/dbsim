"""Deadlock avoidance at a passing loop (M3.3).

Two trains running in opposite directions over a single-track line must **meet**
at a passing loop: one waits on a loop track while the other passes. Done
naively, this **deadlocks** — if both trains default to the *through* track
(track 1, the straight/platform road), each ends up holding a block the other
needs (a circular wait), and neither can move.

This module runs the meet as an event-driven **block-reservation** simulation
(each block is held by at most one train), and avoids the deadlock by **routing
one train onto the passing track**: when a train reaches the loop it is given a
*free* loop track rather than insisting on the through track. That is exactly the
dispatcher's job at a Kreuzungsbahnhof. A naive policy (everyone on the through
track) is kept for contrast — it deadlocks, which the simulation detects (the
event queue drains with trains still stuck).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from dbsim.engine.events import Event
from dbsim.engine.loop import Simulation
from dbsim.model.micro import MicroZone

_REQUEST = "request"  # a train asks for its next block
_CLEAR = "clear"  # a train reaches the end of its current block

_THROUGH = "loop_t1"
_PASSING = "loop_t2"
_LOOP = "LOOP"  # placeholder in a route, resolved to a loop track at run time


@dataclass(frozen=True, slots=True)
class MicroMeetTrain:
    """A train traversing the loop zone in one direction."""

    id: str
    direction: str  # "WE" (west→east) or "EW" (east→west)
    entry_time_s: int = 0
    priority: int = 0


@dataclass(frozen=True, slots=True)
class MeetEvent:
    """A reservation event: a train entering / waiting for / exiting a block."""

    time_s: int
    train_id: str
    block_id: str
    action: str  # "enter" | "leave" | "wait" | "exit"


@dataclass(frozen=True, slots=True)
class MeetResult:
    """Outcome of a meet: who completed, whether it deadlocked, the timeline."""

    completed: frozenset[str]
    deadlocked: bool
    loop_track: dict[str, str]
    events: tuple[MeetEvent, ...]


def _running_time_s(zone: MicroZone, block_id: str) -> int:
    b = zone.block(block_id)
    return max(1, round(b.length_m / (b.max_speed_kmh / 3.6)))


class MicroMeetSimulation:
    """Runs an opposing-train meet over the loop with optional deadlock avoidance."""

    def __init__(
        self,
        zone: MicroZone,
        trains: list[MicroMeetTrain],
        *,
        avoid: bool = True,
        seed: int = 0,
    ) -> None:
        self.zone = zone
        self.avoid = avoid
        self._trains = {t.id: t for t in trains}
        self._route = {t.id: self._route_for(t) for t in trains}
        self._held: dict[str, str] = {}  # block_id -> train_id
        self._current: dict[str, str | None] = {t.id: None for t in trains}
        self._loop_track: dict[str, str] = {}
        self._waiters: dict[str, list[tuple[int, int, str, int]]] = defaultdict(list)
        self._seq = 0
        self._done: set[str] = set()
        self.events: list[MeetEvent] = []

        self.sim = Simulation(seed=seed)
        self.sim.on(_REQUEST, self._on_request)
        self.sim.on(_CLEAR, self._on_clear)
        for t in trains:
            self.sim.schedule_at(float(t.entry_time_s), _REQUEST, train=t.id, step=0)

    @staticmethod
    def _route_for(train: MicroMeetTrain) -> list[str]:
        if train.direction == "WE":
            return ["west_approach", _LOOP, "east_approach"]
        return ["east_approach", _LOOP, "west_approach"]

    # -- block resolution ---------------------------------------------------

    def _target_block(self, train_id: str, step: int) -> str | None:
        """The concrete block a train wants at ``step`` (resolving the loop)."""
        block = self._route[train_id][step]
        if block != _LOOP:
            return block
        if train_id in self._loop_track:
            return self._loop_track[train_id]
        if self.avoid:
            # Take a free loop track (through track preferred), else the other.
            for track in (_THROUGH, _PASSING):
                if self._held.get(track) is None:
                    return track
            return None  # both loop tracks busy → wait
        return _THROUGH  # naive: always the through track (deadlocks on contention)

    # -- handlers -----------------------------------------------------------

    def _on_request(self, sim: Simulation, event: Event) -> None:
        train_id = str(event.payload["train"])
        step = int(event.payload["step"])
        now = int(sim.now)
        route = self._route[train_id]

        if step >= len(route):  # past the last block → exit the zone
            self._release(sim, self._current[train_id], now)
            self._current[train_id] = None
            self._done.add(train_id)
            self.events.append(MeetEvent(now, train_id, "EXIT", "exit"))
            return

        block = self._target_block(train_id, step)
        if block is None or self._held.get(block) not in (None, train_id):
            wait_key = block if block is not None else _LOOP
            self._waiters[wait_key].append(
                (-self._trains[train_id].priority, self._seq, train_id, step)
            )
            self._seq += 1
            self.events.append(MeetEvent(now, train_id, wait_key, "wait"))
            return

        # Acquire the block.
        self._held[block] = train_id
        if self._route[train_id][step] == _LOOP:
            self._loop_track[train_id] = block
        self.events.append(MeetEvent(now, train_id, block, "enter"))
        previous = self._current[train_id]
        self._current[train_id] = block
        if previous is not None and previous != block:
            self._release(sim, previous, now)
        sim.schedule_at(
            float(now + _running_time_s(self.zone, block)), _CLEAR, train=train_id, step=step
        )

    def _on_clear(self, sim: Simulation, event: Event) -> None:
        train_id = str(event.payload["train"])
        step = int(event.payload["step"])
        sim.schedule_at(float(sim.now), _REQUEST, train=train_id, step=step + 1)

    def _release(self, sim: Simulation, block: str | None, now: int) -> None:
        if block is None:
            return
        holder = self._held.pop(block, None)
        if holder is not None:
            self.events.append(MeetEvent(now, holder, block, "leave"))
        # Wake whoever was waiting for this block (and any loop-wide waiters).
        for key in (block, _LOOP) if block in (_THROUGH, _PASSING) else (block,):
            queue = self._waiters.get(key)
            if queue:
                queue.sort()
                _p, _s, train_id, step = queue.pop(0)
                sim.schedule_at(float(now), _REQUEST, train=train_id, step=step)

    # -- running ------------------------------------------------------------

    def run(self) -> MeetResult:
        self.sim.run()
        return MeetResult(
            completed=frozenset(self._done),
            deadlocked=len(self._done) < len(self._trains),
            loop_track=dict(self._loop_track),
            events=tuple(self.events),
        )
