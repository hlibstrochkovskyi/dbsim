"""Tests for declarative disruption scenarios (M2.5)."""

from __future__ import annotations

from pathlib import Path

from dbsim.engine.meso import MesoCorridor, MesoSegment
from dbsim.scenario import (
    ClosureSpec,
    Scenario,
    SpeedRestriction,
    TrainSpec,
    apply_speed_restrictions,
    run_scenario,
    scenario_trains,
)


def _corridor() -> MesoCorridor:
    return MesoCorridor(
        ("A", "B", "C"),
        (MesoSegment(0, "A-B", 600, 1, 120), MesoSegment(1, "B-C", 600, 1, 120)),
    )


def _scenario(**over: object) -> Scenario:
    base: dict[str, object] = dict(
        name="s",
        stations=("A", "B", "C"),
        trains=(
            TrainSpec("FWD", "A", "C", entry_time_s=0, priority=1),
            TrainSpec("BWD", "C", "A", entry_time_s=0, priority=0),
        ),
    )
    base.update(over)
    return Scenario(**base)  # type: ignore[arg-type]


def test_json_round_trip(tmp_path: Path) -> None:
    scenario = _scenario(
        description="demo",
        closures=(ClosureSpec(0, 0, 1000),),
        speed_restrictions=(SpeedRestriction(1, 2.0),),
        dispatcher="fifo",
        seed=7,
    )
    path = tmp_path / "s.json"
    scenario.save(path)
    assert Scenario.load(path) == scenario
    assert Scenario.from_dict(scenario.to_dict()) == scenario


def test_unknown_dispatcher_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown dispatcher"):
        _scenario(dispatcher="nope")


def test_speed_restriction_lengthens_running_time() -> None:
    corridor = apply_speed_restrictions(
        _corridor(), _scenario(speed_restrictions=(SpeedRestriction(1, 2.0),))
    )
    assert corridor.segments[0].running_time_s == 600  # unchanged
    assert corridor.segments[1].running_time_s == 1200  # doubled


def test_trains_routed_in_both_directions() -> None:
    trains = {t.train_id: t for t in scenario_trains(_corridor(), _scenario())}
    assert trains["FWD"].path == (0, 1, 2)
    assert trains["BWD"].path == (2, 1, 0)


def test_run_reproduces_closure() -> None:
    scenario = _scenario(closures=(ClosureSpec(0, 0, 1000),))
    meso = run_scenario(scenario, _corridor())
    # Nothing enters the closed segment 0 during the closure window.
    assert not any(o.segment_index == 0 and o.enter_s < 1000 for o in meso.occupancy)
    assert meso.completed_trains() == {"FWD", "BWD"}
    assert meso.overcapacity_segments() == []


def test_run_is_deterministic() -> None:
    scenario = _scenario(closures=(ClosureSpec(0, 0, 800),))
    a = run_scenario(scenario, _corridor())
    b = run_scenario(scenario, _corridor())
    assert a.movements == b.movements
    assert a.occupancy == b.occupancy


def test_example_scenario_file_parses() -> None:
    path = Path(__file__).parent.parent / "scenarios" / "ammertal-closure.json"
    scenario = Scenario.load(path)
    assert scenario.name == "ammertal-closure"
    assert scenario.stations[0] == "Tübingen Hbf"
    assert len(scenario.trains) == 2
    assert scenario.closures[0].segment == 2
