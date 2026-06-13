"""Tests for the event-driven engine (:mod:`dbsim.engine`)."""

from __future__ import annotations

import pytest

from dbsim.engine import Event, Simulation


def _collect(sim: Simulation) -> list[tuple[float, str]]:
    """Run ``sim`` and return ``(time, kind)`` pairs in processed order."""
    return [(e.time, e.kind) for e in sim.run().events]


def test_events_processed_in_time_order() -> None:
    sim = Simulation()
    sim.schedule_at(3.0, "c")
    sim.schedule_at(1.0, "a")
    sim.schedule_at(2.0, "b")
    assert _collect(sim) == [(1.0, "a"), (2.0, "b"), (3.0, "c")]


def test_equal_time_events_keep_insertion_order() -> None:
    # Tie-break is the insertion sequence — stable FIFO, never Event comparison.
    sim = Simulation()
    for i in range(5):
        sim.schedule_at(1.0, "tick", n=i)
    ns = [int(e.payload["n"]) for e in sim.run().events]
    assert ns == [0, 1, 2, 3, 4]


def test_now_tracks_current_event_time() -> None:
    sim = Simulation()
    seen: list[float] = []
    sim.on("probe", lambda s, _e: seen.append(s.now))
    sim.schedule_at(5.0, "probe")
    sim.schedule_at(10.0, "probe")
    sim.run()
    assert seen == [5.0, 10.0]


def test_handler_can_schedule_future_events() -> None:
    sim = Simulation()

    def chain(s: Simulation, e: Event) -> None:
        n = int(e.payload["n"])
        if n < 3:
            s.schedule(Event(time=s.now + 1.0, kind="tick", payload={"n": n + 1}))

    sim.on("tick", chain)
    sim.schedule_at(0.0, "tick", n=0)
    assert _collect(sim) == [(0.0, "tick"), (1.0, "tick"), (2.0, "tick"), (3.0, "tick")]


def test_scheduling_in_the_past_is_rejected() -> None:
    # No acausal effects: nothing may propagate backward in time.
    sim = Simulation()
    sim.on("late", lambda s, _e: s.schedule(Event(time=s.now - 1.0, kind="oops")))
    sim.schedule_at(5.0, "late")
    with pytest.raises(ValueError, match="acausal"):
        sim.run()


def test_max_time_stops_the_run() -> None:
    sim = Simulation(max_time=2.0)
    for t in (1.0, 2.0, 3.0):
        sim.schedule_at(t, "tick")
    result = sim.run()
    assert [e.time for e in result.events] == [1.0, 2.0]
    assert result.end_time == 2.0


def test_negative_max_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_time"):
        Simulation(max_time=-1.0)


def test_empty_run_is_well_defined() -> None:
    result = Simulation(seed=99).run()
    assert result.events == ()
    assert result.end_time == 0.0
    assert result.seed == 99
