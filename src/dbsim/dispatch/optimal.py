"""Optimal dispatching baseline via CP-SAT (M4.2).

The AMCC heuristic (M4.1) is fast but greedy. To measure how good it is, this
module solves the *same* alternative-graph dispatching instance to **proven
optimality** with OR-Tools CP-SAT, giving the minimum-makespan schedule.

The constraint model mirrors the alternative graph exactly:

- one integer **start** variable per operation;
- **release**: a train's first operation starts no earlier than its release time;
- **train precedence**: an operation starts after its predecessor's running time;
- **resource exclusivity + headway**: each operation reserves its resource for
  ``proc + headway`` via a fixed-size interval, and ``AddNoOverlap`` per resource
  forbids two trains from overlapping there (this is the disjunctive constraint,
  solved exactly rather than by a heuristic arc selection);
- **objective**: minimise the makespan (the maximum train completion time).

The solver is configured for a single worker with a fixed seed, so the optimal
makespan — and the schedule — are deterministic.
"""

from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

from dbsim.dispatch.altgraph import AltGraphProblem, AltGraphSolution, Node


def solve_optimal(problem: AltGraphProblem) -> AltGraphSolution:
    """Solve the dispatching instance to minimum makespan with CP-SAT."""
    model = cp_model.CpModel()
    total_proc = sum(o.proc_s for ops in problem.train_ops.values() for o in ops)
    horizon = (
        max(problem.release.values(), default=0)
        + total_proc
        + problem.headway_s * (sum(len(ops) for ops in problem.train_ops.values()) + 1)
    )

    starts: dict[Node, cp_model.IntVar] = {}
    by_resource: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
    ends: dict[str, cp_model.LinearExpr] = {}

    for train, ops in problem.train_ops.items():
        prev_end: cp_model.LinearExpr | None = None
        for i, op in enumerate(ops):
            s = model.new_int_var(0, horizon, f"s_{train}_{i}")
            starts[train, i] = s
            if i == 0:
                model.add(s >= problem.release[train])
            if prev_end is not None:
                model.add(s >= prev_end)
            prev_end = s + op.proc_s
            interval = model.new_fixed_size_interval_var(
                s, op.proc_s + problem.headway_s, f"iv_{train}_{i}"
            )
            by_resource[op.resource].append(interval)
        ends[train] = starts[train, len(ops) - 1] + ops[-1].proc_s

    for intervals in by_resource.values():
        model.add_no_overlap(intervals)

    makespan = model.new_int_var(0, horizon, "makespan")
    for end in ends.values():
        model.add(makespan >= end)
    model.minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    start = {node: int(solver.value(var)) for node, var in starts.items()}
    completion = {
        train: int(solver.value(starts[train, len(ops) - 1])) + ops[-1].proc_s
        for train, ops in problem.train_ops.items()
    }
    resource_order: dict[str, list[str]] = {}
    res_nodes: dict[str, list[Node]] = defaultdict(list)
    for train, ops in problem.train_ops.items():
        for i, op in enumerate(ops):
            res_nodes[op.resource].append((train, i))
    for res, nodes in res_nodes.items():
        resource_order[res] = [n[0] for n in sorted(nodes, key=lambda n: (start[n], n))]

    return AltGraphSolution(
        start=start,
        completion=completion,
        makespan=int(solver.value(makespan)),
        resource_order=resource_order,
        feasible=feasible,
    )
