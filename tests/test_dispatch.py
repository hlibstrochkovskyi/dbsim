"""Tests for the swappable dispatcher and line closures (M2.4)."""

from __future__ import annotations

from dbsim.dispatch import DISPATCHERS, FifoDispatcher, PriorityDispatcher, SegmentRequest
from dbsim.engine.meso import (
    Closure,
    MesoCorridor,
    MesoSegment,
    MesoSimulation,
    MesoTrain,
)
from dbsim.record import hash_run


def _single(capacity: int = 1, *, running: int = 600, headway: int = 120) -> MesoCorridor:
    return MesoCorridor(("A", "B"), (MesoSegment(0, "A-B", running, capacity, headway),))


def _seg() -> MesoSegment:
    return MesoSegment(0, "A-B", 600, 1, 120)


# -- dispatcher selection ---------------------------------------------------


def test_priority_dispatcher_prefers_higher_priority() -> None:
    waiting = [
        SegmentRequest("low", priority=0, direction=1, requested_at_s=0),
        SegmentRequest("high", priority=5, direction=-1, requested_at_s=10),
    ]
    assert PriorityDispatcher().select(_seg(), waiting, now=20) == "high"


def test_priority_breaks_ties_by_wait_time() -> None:
    waiting = [
        SegmentRequest("late", priority=1, direction=1, requested_at_s=50),
        SegmentRequest("early", priority=1, direction=1, requested_at_s=10),
    ]
    assert PriorityDispatcher().select(_seg(), waiting, now=60) == "early"


def test_fifo_dispatcher_ignores_priority() -> None:
    waiting = [
        SegmentRequest("first", priority=0, direction=1, requested_at_s=5),
        SegmentRequest("second", priority=9, direction=-1, requested_at_s=10),
    ]
    assert FifoDispatcher().select(_seg(), waiting, now=20) == "first"


def test_empty_queue_selects_none() -> None:
    assert PriorityDispatcher().select(_seg(), [], now=0) is None


# -- closures (line-closure disruption) -------------------------------------


def test_closure_holds_trains_then_reopens() -> None:
    corridor = _single(running=600, headway=120)
    fwd = MesoTrain("FWD", (0, 1), entry_time_s=0, priority=1)
    bwd = MesoTrain("BWD", (1, 0), entry_time_s=0, priority=0)
    meso = MesoSimulation(corridor, [fwd, bwd], closures=[Closure(0, 0, 1000)])
    meso.run()

    # Nothing enters the segment during the closure window.
    assert all(o.enter_s >= 1000 for o in meso.occupancy)
    # Both trains still complete, and there is never an over-capacity conflict.
    assert meso.completed_trains() == {"FWD", "BWD"}
    assert meso.overcapacity_segments() == []


def test_closure_stays_conflict_free_on_single_track() -> None:
    corridor = _single(running=600, headway=120)
    trains = [MesoTrain(f"T{i}", (0, 1), entry_time_s=0) for i in range(3)]
    meso = MesoSimulation(corridor, trains, closures=[Closure(0, 0, 500)])
    meso.run()
    assert meso.max_occupancy(0) == 1  # single-track exclusion preserved through closure


# -- pluggability -----------------------------------------------------------


def test_dispatcher_choice_changes_the_outcome() -> None:
    # Under a closure both trains queue; the dispatcher decides who goes first.
    corridor = _single(running=600, headway=120)
    fwd = MesoTrain("FWD", (0, 1), entry_time_s=0, priority=1)
    bwd = MesoTrain("BWD", (1, 0), entry_time_s=0, priority=0)
    closure = [Closure(0, 0, 1000)]

    def first_out(dispatcher_name: str) -> str:
        meso = MesoSimulation(
            corridor, [fwd, bwd], dispatcher=DISPATCHERS[dispatcher_name](), closures=closure
        )
        meso.run()
        return min(meso.occupancy, key=lambda o: o.enter_s).train_id

    # Priority sends the high-priority FWD first; FIFO breaks the tie by id (BWD).
    assert first_out("priority") == "FWD"
    assert first_out("fifo") == "BWD"


def test_dispatched_run_is_deterministic() -> None:
    corridor = _single()
    trains = [MesoTrain("FWD", (0, 1), 0, 1), MesoTrain("BWD", (1, 0), 0, 0)]
    closure = [Closure(0, 0, 800)]
    a = MesoSimulation(corridor, trains, closures=closure).run()
    b = MesoSimulation(corridor, trains, closures=closure).run()
    assert hash_run(a) == hash_run(b)
