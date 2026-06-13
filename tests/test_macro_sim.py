"""Tests for the macroscopic train simulation (:mod:`dbsim.engine.trains`)."""

from __future__ import annotations

from pathlib import Path

from dbsim.engine import MacroSimulation, TrainSchedule, load_schedules
from dbsim.model import Timetable
from dbsim.record import hash_run

WEDNESDAY = 20260617
H = 3600


def _schedules(db: Path) -> list[TrainSchedule]:
    with Timetable(db) as tt:
        return load_schedules(tt, WEDNESDAY)


def test_load_schedules_reads_full_stop_sequences(mini_db: Path) -> None:
    schedules = {s.trip_id: s for s in _schedules(mini_db)}
    assert set(schedules) == {"T_ICE_A", "T_NIGHT"}
    ice = schedules["T_ICE_A"]
    assert [s.seq for s in ice.stops] == [0, 1, 2]
    assert ice.stops[0].departure_s == 8 * H
    assert ice.stops[0].arrival_s is None  # origin has no arrival


def test_zero_perturbation_reproduces_schedule(mini_db: Path) -> None:
    macro = MacroSimulation(_schedules(mini_db), seed=0)
    macro.run()
    assert macro.reproduces_schedule()
    assert macro.max_abs_deviation_s() == 0
    # Every record's simulated time equals its scheduled time.
    for r in macro.records:
        if r.scheduled_s is not None:
            assert r.time_s == r.scheduled_s


def test_records_cover_arrivals_and_departures(mini_db: Path) -> None:
    macro = MacroSimulation(_schedules(mini_db), seed=0)
    macro.run()
    # 2 trains x 3 stops, origin = depart only, terminus = arrive only,
    # middle = arrive + depart → 4 events per train.
    assert len(macro.records) == 8
    ice = [r for r in macro.records if r.trip_id == "T_ICE_A"]
    assert [(r.event, r.seq) for r in ice] == [
        ("depart", 0),
        ("arrive", 1),
        ("depart", 1),
        ("arrive", 2),
    ]


def test_simulation_handles_past_midnight(mini_db: Path) -> None:
    macro = MacroSimulation(_schedules(mini_db), seed=0)
    macro.run()
    night_arr = [r for r in macro.records if r.trip_id == "T_NIGHT" and r.event == "arrive"]
    # Final arrival at 25:10 = 90600s, reproduced exactly.
    assert night_arr[-1].time_s == 25 * H + 10 * 60


def test_simulation_is_deterministic(mini_db: Path) -> None:
    a = MacroSimulation(_schedules(mini_db), seed=0).run()
    b = MacroSimulation(_schedules(mini_db), seed=0).run()
    assert hash_run(a) == hash_run(b)


def test_min_dwell_can_delay_departures(mini_db: Path) -> None:
    # A large minimum dwell pushes departures later than scheduled → deviations.
    macro = MacroSimulation(_schedules(mini_db), seed=0, min_dwell_s=10 * 60)
    macro.run()
    assert not macro.reproduces_schedule()
    assert macro.max_abs_deviation_s() > 0


def test_empty_when_no_active_services(mini_db: Path) -> None:
    with Timetable(mini_db) as tt:
        # A date outside every calendar range → no active services, no trains.
        schedules = load_schedules(tt, 20200101)
    assert schedules == []
    assert MacroSimulation(schedules, seed=0).run().events == ()
