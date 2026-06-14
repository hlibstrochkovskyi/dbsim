"""Tests for microscopic movement & blocking-time (M3.2).

Includes the micro-engine **determinism** test required from this layer's first
commit. Uses a small synthetic micro zone, so it runs with no network.
"""

from __future__ import annotations

from pathlib import Path

from dbsim.analysis.stairway import StairwayTrain, minimum_headway_s, render_stairway
from dbsim.engine.blocking import TrainDynamics, blocking_times, micro_trajectory
from dbsim.model.micro import APPROACH, LOOP, Block, MicroRoute, MicroZone


def _zone() -> MicroZone:
    # Two 500 m approaches at 70 km/h, a 300 m loop track at 50 km/h.
    return MicroZone(
        name="test",
        west_boundary="W",
        east_boundary="E",
        blocks=(
            Block("appr_w", 500, 70, False, APPROACH),
            Block("loop_t1", 300, 50, True, LOOP),
            Block("appr_e", 500, 70, False, APPROACH),
        ),
        routes=(MicroRoute("WE", "west_to_east", "1", ("appr_w", "loop_t1", "appr_e")),),
        signals=(),
        switches=(),
    )


def _route() -> MicroRoute:
    return _zone().routes[0]


def test_trajectory_accelerates_from_rest() -> None:
    traj = micro_trajectory(
        _zone(), _route(), TrainDynamics(), entry_speed_ms=0.0, exit_speed_ms=999
    )
    assert traj[0].enter_speed_ms == 0.0
    assert traj[0].exit_speed_ms > 0.0  # accelerated over the first block
    # Times strictly increase block to block.
    times = [t.enter_s for t in traj] + [traj[-1].exit_s]
    assert times == sorted(times)


def test_speed_respects_block_limits() -> None:
    traj = micro_trajectory(
        _zone(), _route(), TrainDynamics(), entry_speed_ms=999, exit_speed_ms=999
    )
    by_id = {t.block_id: t for t in traj}
    # On the loop the speed cannot exceed 50 km/h (≈ 13.9 m/s).
    assert by_id["loop_t1"].exit_speed_ms <= 50 / 3.6 + 1e-6


def test_braking_to_a_stop() -> None:
    traj = micro_trajectory(
        _zone(), _route(), TrainDynamics(), entry_speed_ms=0.0, exit_speed_ms=0.0
    )
    assert traj[-1].exit_speed_ms == 0.0  # brakes to a stop at the route end


def test_train_max_speed_caps_line_speed() -> None:
    slow = TrainDynamics(max_speed_kmh=40)
    traj = micro_trajectory(_zone(), _route(), slow, entry_speed_ms=999, exit_speed_ms=999)
    assert all(t.exit_speed_ms <= 40 / 3.6 + 1e-6 for t in traj)


def test_blocking_interval_structure() -> None:
    traj = micro_trajectory(
        _zone(), _route(), TrainDynamics(), entry_speed_ms=999, exit_speed_ms=999
    )
    blocking = blocking_times(traj, TrainDynamics())
    assert [b.block_id for b in blocking] == ["appr_w", "loop_t1", "appr_e"]
    for b in blocking:
        assert b.start_s < b.end_s
    # Blocking starts advance block to block (the stairway steps up in time).
    starts = [b.start_s for b in blocking]
    assert starts == sorted(starts)
    # A block is reserved before the train enters it (approach + setup) and
    # released after it exits (clearing + release).
    for tr, bi in zip(traj, blocking, strict=True):
        assert bi.start_s <= tr.enter_s
        assert bi.end_s >= tr.exit_s


def test_minimum_headway_is_the_critical_block() -> None:
    blocking = blocking_times(
        micro_trajectory(_zone(), _route(), TrainDynamics(), entry_speed_ms=999, exit_speed_ms=999),
        TrainDynamics(),
    )
    headway = minimum_headway_s(blocking, blocking)  # identical follower at offset 0
    assert headway == max(b.duration_s for b in blocking)  # critical (longest) block sets it


def test_micro_engine_is_deterministic() -> None:
    a = micro_trajectory(_zone(), _route(), TrainDynamics(), entry_speed_ms=999, exit_speed_ms=999)
    b = micro_trajectory(_zone(), _route(), TrainDynamics(), entry_speed_ms=999, exit_speed_ms=999)
    assert a == b
    assert blocking_times(a, TrainDynamics()) == blocking_times(b, TrainDynamics())


def test_stairway_renders(tmp_path: Path) -> None:
    traj = micro_trajectory(
        _zone(), _route(), TrainDynamics(), entry_speed_ms=999, exit_speed_ms=999
    )
    blocking = blocking_times(traj, TrainDynamics())
    out = render_stairway(
        [StairwayTrain("t", tuple(traj), tuple(blocking))], tmp_path / "s.png", title="t"
    )
    assert out.exists() and out.stat().st_size > 0
