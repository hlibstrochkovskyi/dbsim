"""Tests for deadlock avoidance at a passing loop (M3.3).

Uses a synthetic micro zone (no network) with the block ids the meet sim expects.
"""

from __future__ import annotations

from dbsim.engine.micro_meet import MicroMeetSimulation, MicroMeetTrain
from dbsim.model.micro import APPROACH, LOOP, Block, MicroRoute, MicroZone


def _loop_zone() -> MicroZone:
    return MicroZone(
        name="loop",
        west_boundary="W",
        east_boundary="E",
        blocks=(
            Block("west_approach", 500, 70, False, APPROACH),
            Block("loop_t1", 300, 70, True, LOOP),
            Block("loop_t2", 300, 50, False, LOOP),
            Block("east_approach", 500, 70, False, APPROACH),
        ),
        routes=(
            MicroRoute("WE_t1", "west_to_east", "1", ("west_approach", "loop_t1", "east_approach")),
            MicroRoute("EW_t2", "east_to_west", "2", ("east_approach", "loop_t2", "west_approach")),
        ),
        signals=(),
        switches=(),
    )


def _opposing() -> list[MicroMeetTrain]:
    return [
        MicroMeetTrain("A", "WE", entry_time_s=0, priority=1),
        MicroMeetTrain("B", "EW", entry_time_s=0, priority=0),
    ]


def test_naive_policy_deadlocks() -> None:
    # Both default to the through track → circular wait.
    result = MicroMeetSimulation(_loop_zone(), _opposing(), avoid=False).run()
    assert result.deadlocked
    assert result.completed == frozenset()  # neither train escapes


def test_avoidance_resolves_the_meet() -> None:
    result = MicroMeetSimulation(_loop_zone(), _opposing(), avoid=True).run()
    assert not result.deadlocked
    assert result.completed == {"A", "B"}
    # The trains meet on *different* loop tracks.
    assert result.loop_track["A"] != result.loop_track["B"]
    assert set(result.loop_track.values()) == {"loop_t1", "loop_t2"}


def test_no_block_is_ever_double_occupied() -> None:
    result = MicroMeetSimulation(_loop_zone(), _opposing(), avoid=True).run()
    occupied: dict[str, str] = {}
    for ev in result.events:
        if ev.action == "enter":
            assert ev.block_id not in occupied  # capacity 1: never double-occupied
            occupied[ev.block_id] = ev.train_id
        elif ev.action == "leave":
            assert occupied.pop(ev.block_id) == ev.train_id


def test_meet_is_deterministic() -> None:
    a = MicroMeetSimulation(_loop_zone(), _opposing(), avoid=True).run()
    b = MicroMeetSimulation(_loop_zone(), _opposing(), avoid=True).run()
    assert a == b


def test_single_train_passes_through() -> None:
    result = MicroMeetSimulation(_loop_zone(), [MicroMeetTrain("solo", "WE")], avoid=True).run()
    assert result.completed == {"solo"}
    assert not result.deadlocked


def test_same_direction_trains_both_complete() -> None:
    trains = [
        MicroMeetTrain("first", "WE", entry_time_s=0, priority=1),
        MicroMeetTrain("second", "WE", entry_time_s=10, priority=0),
    ]
    result = MicroMeetSimulation(_loop_zone(), trains, avoid=True).run()
    assert result.completed == {"first", "second"}
    assert not result.deadlocked
