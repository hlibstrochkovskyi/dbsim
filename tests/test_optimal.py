"""Tests for the CP-SAT optimal dispatching baseline (M4.2)."""

from __future__ import annotations

from itertools import pairwise

from dbsim.dispatch.altgraph import AltGraphProblem, Operation, solve_amcc
from dbsim.dispatch.optimal import solve_optimal


def _meet_problem(delay: int = 1000) -> AltGraphProblem:
    return AltGraphProblem(
        train_ops={
            "H": [Operation("H", "AB", 600), Operation("H", "BC", 600)],
            "L": [Operation("L", "BC", 600), Operation("L", "AB", 600)],
        },
        release={"H": delay, "L": 0},
        headway_s=120,
    )


def _benchmark_problem() -> AltGraphProblem:
    # Reproducible (random seed 77) 3-train, 3-resource instance where AMCC is
    # greedy and leaves a quantifiable gap below the optimum.
    return AltGraphProblem(
        train_ops={
            "A": [Operation("A", "S2", 300), Operation("A", "S1", 300), Operation("A", "S3", 600)],
            "B": [Operation("B", "S1", 600), Operation("B", "S3", 300)],
            "C": [Operation("C", "S1", 300), Operation("C", "S2", 600)],
        },
        release={"A": 200, "B": 200, "C": 200},
        headway_s=120,
    )


def _is_feasible(problem: AltGraphProblem, sol: dict[tuple[str, int], int]) -> bool:
    # Train precedence: each op starts after its predecessor's running time.
    for ops in problem.train_ops.values():
        for i in range(1, len(ops)):
            train = ops[i].train_id
            if sol[train, i] < sol[train, i - 1] + ops[i - 1].proc_s:
                return False
    # Release on the first op.
    for train in problem.train_ops:
        if sol[train, 0] < problem.release[train]:
            return False
    # Resource exclusivity + headway: no two reservations overlap on a resource.
    by_res: dict[str, list[tuple[int, int]]] = {}
    for train, ops in problem.train_ops.items():
        for i, op in enumerate(ops):
            s = sol[train, i]
            by_res.setdefault(op.resource, []).append((s, s + op.proc_s + problem.headway_s))
    for spans in by_res.values():
        spans.sort()
        for (_, e1), (s2, _) in pairwise(spans):
            if s2 < e1:
                return False
    return True


def test_optimal_is_feasible_and_respects_constraints() -> None:
    problem = _meet_problem()
    sol = solve_optimal(problem)
    assert sol.feasible
    assert _is_feasible(problem, sol.start)


def test_optimal_single_train_is_the_free_run() -> None:
    problem = AltGraphProblem(
        train_ops={"T": [Operation("T", "AB", 600), Operation("T", "BC", 300)]},
        release={"T": 100},
    )
    sol = solve_optimal(problem)
    assert sol.makespan == 100 + 600 + 300


def test_amcc_is_optimal_on_the_meet() -> None:
    # On the canonical two-train meet, the greedy heuristic is provably optimal.
    problem = _meet_problem(delay=1000)
    assert solve_amcc(problem).makespan == solve_optimal(problem).makespan == 2320


def test_amcc_has_a_quantified_gap_on_harder_instance() -> None:
    problem = _benchmark_problem()
    amcc = solve_amcc(problem)
    opt = solve_optimal(problem)
    # CP-SAT is a valid lower bound: the heuristic can never beat it.
    assert opt.makespan <= amcc.makespan
    # On this instance the greedy heuristic is strictly suboptimal.
    assert opt.makespan == 1940
    assert amcc.makespan - opt.makespan == 300


def test_optimal_is_deterministic() -> None:
    problem = _benchmark_problem()
    assert solve_optimal(problem) == solve_optimal(problem)
