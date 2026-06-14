"""Tests for macro–micro coupling (M3.4), incl. the boundary-consistency test."""

from __future__ import annotations

from dbsim.engine.coupling import BoundaryArrival, couple_zone
from dbsim.model.micro import APPROACH, LOOP, Block, MicroRoute, MicroZone


def _zone() -> MicroZone:
    return MicroZone(
        name="loop",
        west_boundary="Unterjesingen Mitte",
        east_boundary="Entringen",
        blocks=(
            Block("west_approach", 500, 70, False, APPROACH),
            Block("loop_t1", 300, 70, True, LOOP),
            Block("loop_t2", 300, 50, False, LOOP),
            Block("east_approach", 500, 70, False, APPROACH),
        ),
        routes=(
            MicroRoute("WE_t1", "west_to_east", "1", ("west_approach", "loop_t1", "east_approach")),
        ),
        signals=(),
        switches=(),
    )


def test_handoff_boundaries_match_direction() -> None:
    result = couple_zone(
        _zone(),
        [BoundaryArrival("we", "WE", 1000), BoundaryArrival("ew", "EW", 5000)],
    )
    by_id = {h.train_id: h for h in result.handoffs}
    assert (by_id["we"].entry_boundary, by_id["we"].exit_boundary) == (
        "Unterjesingen Mitte",
        "Entringen",
    )
    assert (by_id["ew"].entry_boundary, by_id["ew"].exit_boundary) == (
        "Entringen",
        "Unterjesingen Mitte",
    )


def test_handoffs_are_time_consistent() -> None:
    # The boundary-consistency check: micro entry is never before the macro
    # arrival, and the exit is after the entry.
    result = couple_zone(
        _zone(),
        [BoundaryArrival("a", "WE", 1000), BoundaryArrival("b", "EW", 1010)],
    )
    assert result.consistent
    for h in result.handoffs:
        assert h.entry_time_s >= h.macro_arrival_s  # no time travel
        assert h.exit_time_s > h.entry_time_s


def test_opposing_trains_pass_without_waiting() -> None:
    # The loop separates opposing trains: both pass, neither held at the boundary.
    result = couple_zone(
        _zone(),
        [BoundaryArrival("we", "WE", 1000), BoundaryArrival("ew", "EW", 1010)],
    )
    assert not result.deadlocked and result.consistent
    assert all(h.boundary_wait_s == 0 for h in result.handoffs)


def test_micro_contention_propagates_to_macro() -> None:
    # A close follower is held at the boundary by the leader's block; that micro
    # delay shows up in its macro exit time.
    coupled = couple_zone(
        _zone(),
        [BoundaryArrival("lead", "WE", 1000), BoundaryArrival("follow", "WE", 1010)],
    )
    follow = next(h for h in coupled.handoffs if h.train_id == "follow")
    solo = couple_zone(_zone(), [BoundaryArrival("follow", "WE", 1010)]).handoffs[0]
    assert follow.boundary_wait_s > 0  # held by micro contention
    assert follow.exit_time_s > solo.exit_time_s  # propagates to the macro exit


def test_deadlock_is_inconsistent() -> None:
    # The naive policy deadlocks opposing trains → the coupling is not consistent.
    result = couple_zone(
        _zone(),
        [BoundaryArrival("we", "WE", 1000), BoundaryArrival("ew", "EW", 1000)],
        avoid=False,
    )
    assert result.deadlocked
    assert not result.consistent


def test_coupled_run_is_deterministic() -> None:
    arrivals = [BoundaryArrival("a", "WE", 1000), BoundaryArrival("b", "EW", 1010)]
    assert couple_zone(_zone(), arrivals) == couple_zone(_zone(), arrivals)
