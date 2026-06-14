"""Tests for the alternative-graph dispatcher v2 (M4.1)."""

from __future__ import annotations

from dbsim.dispatch.altgraph import (
    AltGraphDispatcher,
    AltGraphProblem,
    Operation,
    build_problem_from_meso,
    solve_amcc,
    solve_by_priority,
)
from dbsim.dispatch.base import SegmentRequest
from dbsim.engine.meso import MesoCorridor, MesoSegment, MesoTrain


def _meet_problem(delay: int = 1000) -> AltGraphProblem:
    # High-priority train delayed; on-time opposing train; two single-track segs.
    return AltGraphProblem(
        train_ops={
            "H": [Operation("H", "AB", 600), Operation("H", "BC", 600)],
            "L": [Operation("L", "BC", 600), Operation("L", "AB", 600)],
        },
        release={"H": delay, "L": 0},
        headway_s=120,
    )


def _total_delay(sol: object, problem: AltGraphProblem) -> int:
    free = {
        t: problem.release[t] + sum(o.proc_s for o in problem.train_ops[t])
        for t in problem.train_ops
    }
    return sum(sol.completion[t] - free[t] for t in free)  # type: ignore[attr-defined]


def test_single_train_schedule_is_the_free_run() -> None:
    problem = AltGraphProblem(
        train_ops={"T": [Operation("T", "AB", 600), Operation("T", "BC", 300)]},
        release={"T": 100},
    )
    sol = solve_amcc(problem)
    assert sol.completion["T"] == 100 + 600 + 300  # release + processing
    assert sol.makespan == 1000


def test_amcc_never_worse_than_priority() -> None:
    problem = _meet_problem()
    priority = {"H": 10, "L": 0}
    assert solve_amcc(problem).makespan <= solve_by_priority(problem, priority).makespan


def test_amcc_strictly_beats_priority_on_delayed_meet() -> None:
    problem = _meet_problem(delay=1000)
    priority = {"H": 10, "L": 0}
    v1 = solve_by_priority(problem, priority)
    v2 = solve_amcc(problem)
    # The decisions differ and v2 is better on makespan and total delay.
    assert v1.resource_order["BC"] != v2.resource_order["BC"]
    assert v2.makespan < v1.makespan
    assert _total_delay(v2, problem) < _total_delay(v1, problem)


def test_priority_orders_by_priority_when_feasible() -> None:
    # Same-direction following: higher priority goes first.
    problem = AltGraphProblem(
        train_ops={"A": [Operation("A", "S", 300)], "B": [Operation("B", "S", 300)]},
        release={"A": 0, "B": 0},
        headway_s=60,
    )
    sol = solve_by_priority(problem, {"A": 1, "B": 5})
    assert sol.resource_order["S"] == ["B", "A"]  # B higher priority → first


def test_solution_is_deterministic() -> None:
    problem = _meet_problem()
    assert solve_amcc(problem) == solve_amcc(problem)


def test_build_problem_from_meso() -> None:
    corridor = MesoCorridor(
        ("A", "B", "C"),
        (MesoSegment(0, "A-B", 600, 1, 120), MesoSegment(1, "B-C", 500, 1, 120)),
    )
    trains = [MesoTrain("we", (0, 1, 2), 0), MesoTrain("ew", (2, 1, 0), 100)]
    problem = build_problem_from_meso(corridor, trains)
    assert [o.resource for o in problem.train_ops["we"]] == ["0", "1"]
    assert [o.resource for o in problem.train_ops["ew"]] == ["1", "0"]  # reverse order
    assert problem.train_ops["we"][1].proc_s == 500  # B-C running time
    assert problem.release == {"we": 0, "ew": 100}


def test_altgraph_dispatcher_follows_order() -> None:
    dispatcher = AltGraphDispatcher({"0": ["L", "H"]})
    seg = MesoSegment(0, "A-B", 600, 1, 120)
    waiting = [
        SegmentRequest("H", priority=10, direction=1, requested_at_s=0),
        SegmentRequest("L", priority=0, direction=-1, requested_at_s=5),
    ]
    # The alt-graph order puts L first despite H's higher priority / earlier wait.
    assert dispatcher.select(seg, waiting, now=10) == "L"
