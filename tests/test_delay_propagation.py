"""Tests for delay propagation (M1.2): primary delays, dwell recovery, connections.

These use hand-built schedules for precise control over timing, plus the
``gtfs_mini`` fixture for a feed-driven case.
"""

from __future__ import annotations

from pathlib import Path

from dbsim.engine import (
    Connection,
    MacroSimulation,
    MovementRecord,
    PrimaryDelay,
    ScheduledStop,
    TrainSchedule,
    load_schedules,
)
from dbsim.model import Timetable
from dbsim.record import hash_run

WEDNESDAY = 20260617
H = 3600


def _three_stop_train(trip: str, t0: int, run1: int, dwell: int, run2: int) -> TrainSchedule:
    """A train P -(run1)-> S -(dwell)-> -(run2)-> Q starting at t0."""
    a_s = t0 + run1
    d_s = a_s + dwell
    return TrainSchedule(
        trip,
        trip,
        (
            ScheduledStop(0, "P", None, t0),
            ScheduledStop(1, "S", a_s, d_s),
            ScheduledStop(2, "Q", d_s + run2, None),
        ),
    )


def _dep(records: list[MovementRecord], trip: str, seq: int) -> int:
    return next(
        r.time_s for r in records if r.trip_id == trip and r.event == "depart" and r.seq == seq
    )


def _arr(records: list[MovementRecord], trip: str, seq: int) -> int:
    return next(
        r.time_s for r in records if r.trip_id == trip and r.event == "arrive" and r.seq == seq
    )


# -- primary delay + propagation -------------------------------------------


def test_primary_delay_propagates_downstream() -> None:
    train = _three_stop_train("T", t0=0, run1=100, dwell=0, run2=100)  # S arr/dep 100, Q 200
    macro = MacroSimulation([train], seed=0, primary_delays=[PrimaryDelay("T", 0, 50)])
    macro.run()
    # Origin held 50s; with no dwell slack the delay carries unchanged downstream.
    assert _dep(macro.records, "T", 0) == 50
    assert _arr(macro.records, "T", 1) == 150
    assert _arr(macro.records, "T", 2) == 250


def test_dwell_slack_recovers_part_of_the_delay() -> None:
    # 300s scheduled dwell at S; min_dwell 0 → a 50s incoming delay is absorbed.
    train = _three_stop_train("T", t0=0, run1=100, dwell=300, run2=100)
    macro = MacroSimulation([train], seed=0, primary_delays=[PrimaryDelay("T", 0, 50)])
    macro.run()
    assert _arr(macro.records, "T", 1) == 150  # arrives 50 late
    assert _dep(macro.records, "T", 1) == 400  # but departs on time (sched dep 400)
    assert _arr(macro.records, "T", 2) == 500  # fully recovered


def test_min_dwell_limits_recovery() -> None:
    train = _three_stop_train("T", t0=0, run1=100, dwell=300, run2=100)
    macro = MacroSimulation(
        [train], seed=0, min_dwell_s=280, primary_delays=[PrimaryDelay("T", 0, 50)]
    )
    macro.run()
    # Arrives at 150; min dwell 280 → cannot leave before 430 (sched dep 400) → 30s late.
    assert _dep(macro.records, "T", 1) == 430


def test_no_acausal_no_upstream_change() -> None:
    # Delaying a mid-trip stop must never change earlier stops, nor produce a
    # negative deviation anywhere.
    train = _three_stop_train("T", t0=1000, run1=100, dwell=50, run2=100)
    baseline = MacroSimulation([train], seed=0)
    baseline.run()
    delayed = MacroSimulation([train], seed=0, primary_delays=[PrimaryDelay("T", 1, 200)])
    delayed.run()
    assert _dep(delayed.records, "T", 0) == _dep(baseline.records, "T", 0)  # origin unchanged
    assert _arr(delayed.records, "T", 1) == _arr(baseline.records, "T", 1)  # arrival unchanged
    assert all((r.deviation_s or 0) >= 0 for r in delayed.records)  # nothing early


# -- connection holding -----------------------------------------------------


def _connection_case(feeder_delay: int) -> tuple[int, int]:
    """Run the feeder/connector scenario; return connector (dep_time, deviation)."""
    connector = TrainSchedule(
        "C",
        "C",
        (
            ScheduledStop(0, "P", None, 0),
            ScheduledStop(1, "S", 100, 110),
            ScheduledStop(2, "Q", 200, None),
        ),
    )
    feeder = TrainSchedule(
        "F", "F", (ScheduledStop(0, "Y", None, 0), ScheduledStop(1, "S", 85, None))
    )
    conn = Connection("F", "S", "C", 1, min_transfer_s=20, max_wait_s=600)
    macro = MacroSimulation(
        [connector, feeder],
        seed=0,
        connections=[conn],
        primary_delays=[PrimaryDelay("F", 0, feeder_delay)] if feeder_delay else [],
    )
    macro.run()
    rec = next(r for r in macro.records if r.trip_id == "C" and r.event == "depart" and r.seq == 1)
    return rec.time_s, rec.deviation_s or 0


def test_connection_not_held_when_feeder_on_time() -> None:
    # Feeder arrives 85; transfer 20 → 105 <= sched dep 110, so no hold.
    assert _connection_case(feeder_delay=0) == (110, 0)


def test_connection_held_for_late_feeder() -> None:
    # Feeder arrives 115; connector held to 115 + 20 = 135.
    assert _connection_case(feeder_delay=30) == (135, 25)


def test_connection_held_up_to_window() -> None:
    # Feeder arrives 685; 685 + 20 = 705 <= 110 + 600 → still caught.
    assert _connection_case(feeder_delay=600) == (705, 595)


def test_connection_dropped_past_max_wait() -> None:
    # Feeder arrives 695; 695 + 20 = 715 > 110 + 600 → dropped; connector leaves
    # at the max_wait deadline 710.
    assert _connection_case(feeder_delay=610) == (710, 600)


# -- feed-driven + determinism ---------------------------------------------


def test_injected_delay_on_fixture_propagates(mini_db: Path) -> None:
    with Timetable(mini_db) as tt:
        schedules = load_schedules(tt, WEDNESDAY)
    macro = MacroSimulation(schedules, seed=0, primary_delays=[PrimaryDelay("T_ICE_A", 0, 1200)])
    macro.run()
    assert not macro.reproduces_schedule()
    assert macro.max_abs_deviation_s() == 1200
    assert macro.worst_trains(1) == [("T_ICE_A", 1200)]
    # T_NIGHT was not delayed.
    night = [r for r in macro.records if r.trip_id == "T_NIGHT"]
    assert all((r.deviation_s or 0) == 0 for r in night)


def test_delayed_run_is_deterministic(mini_db: Path) -> None:
    with Timetable(mini_db) as tt:
        schedules = load_schedules(tt, WEDNESDAY)
    delays = [PrimaryDelay("T_ICE_A", 0, 1200)]
    a = MacroSimulation(schedules, seed=0, primary_delays=delays).run()
    b = MacroSimulation(schedules, seed=0, primary_delays=delays).run()
    assert hash_run(a) == hash_run(b)
