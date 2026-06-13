"""Tests for the recording format & replay (:mod:`dbsim.record.recording`)."""

from __future__ import annotations

from pathlib import Path

from dbsim.engine import MacroSimulation, PrimaryDelay, ScheduledStop, TrainSchedule, load_schedules
from dbsim.model import Timetable
from dbsim.record import Recording, hash_run, load_recording, write_recording

WEDNESDAY = 20260617


def _hand_train() -> TrainSchedule:
    # P depart 0, S arrive 100 / depart 150, Q arrive 250.
    return TrainSchedule(
        "T",
        "T",
        (
            ScheduledStop(0, "P", None, 0),
            ScheduledStop(1, "S", 100, 150),
            ScheduledStop(2, "Q", 250, None),
        ),
    )


def test_round_trip_is_identical(tmp_path: Path) -> None:
    macro = MacroSimulation([_hand_train()], seed=0)
    run_hash = hash_run(macro.run())
    path = tmp_path / "rec.parquet"
    meta = write_recording(macro.records, path, service_date=20260616, seed=0, run_hash=run_hash)

    rec = load_recording(path)
    # The reloaded movement stream equals the in-memory one exactly.
    assert rec.movements == tuple(macro.records)
    assert rec.meta == meta
    assert rec.meta.run_hash == run_hash
    assert rec.meta.n_events == len(macro.records)


def test_analysis_is_identical_after_reload(tmp_path: Path) -> None:
    macro = MacroSimulation([_hand_train()], seed=0, primary_delays=[PrimaryDelay("T", 0, 60)])
    macro.run()
    path = tmp_path / "rec.parquet"
    write_recording(macro.records, path, service_date=20260616, seed=0, run_hash="h")

    rec = load_recording(path)
    # A derived metric computed from the reload matches the live simulation.
    live_total = macro.total_delay_s()
    reloaded_total = sum(d for m in rec.movements if (d := m.deviation_s or 0) > 0)
    assert reloaded_total == live_total


def test_position_before_start_is_none(tmp_path: Path) -> None:
    rec = _written(tmp_path)
    assert rec.position_at("T", -1) is None


def test_position_while_moving(tmp_path: Path) -> None:
    rec = _written(tmp_path)
    p = rec.position_at("T", 50)  # halfway P -> S (0..100)
    assert p is not None and not p.at_stop
    assert (p.from_stop_id, p.to_stop_id) == ("P", "S")
    assert p.fraction == 0.5


def test_position_while_dwelling(tmp_path: Path) -> None:
    rec = _written(tmp_path)
    p = rec.position_at("T", 120)  # dwelling at S (100..150)
    assert p is not None and p.at_stop
    assert p.from_stop_id == "S"


def test_position_after_terminus(tmp_path: Path) -> None:
    rec = _written(tmp_path)
    p = rec.position_at("T", 9999)
    assert p is not None and p.at_stop
    assert p.from_stop_id == "Q"


def test_position_xy_interpolates(tmp_path: Path) -> None:
    rec = _written(tmp_path)
    coords = {"P": (0.0, 0.0), "S": (10.0, 20.0), "Q": (0.0, 0.0)}
    xy = rec.position_xy_at("T", 50, coords)  # halfway P -> S
    assert xy == (5.0, 10.0)


def test_recording_round_trips_real_feed(mini_db: Path, tmp_path: Path) -> None:
    with Timetable(mini_db) as tt:
        schedules = load_schedules(tt, WEDNESDAY)
    macro = MacroSimulation(schedules, seed=0)
    run_hash = hash_run(macro.run())
    path = tmp_path / "mini.parquet"
    write_recording(macro.records, path, service_date=WEDNESDAY, seed=0, run_hash=run_hash)

    rec = load_recording(path)
    assert rec.movements == tuple(macro.records)
    assert set(rec.trips()) == {"T_ICE_A", "T_NIGHT"}


def _written(tmp_path: Path) -> Recording:
    macro = MacroSimulation([_hand_train()], seed=0)
    macro.run()
    path = tmp_path / "rec.parquet"
    write_recording(macro.records, path, service_date=20260616, seed=0, run_hash="h")
    return load_recording(path)
