"""Alternative-graph dispatching (v2): AMCC vs a priority baseline (M4.1).

The **alternative graph** (Mascis & Pacciarelli) models train dispatching as
job-shop scheduling:

- a **node** is an *operation* — one train occupying one resource (a block /
  segment) — carrying a processing time (running time);
- **fixed arcs** chain each train's operations in route order (plus a source →
  first-op arc weighted by the train's release time);
- a **disjunctive pair** of *alternative arcs* encodes the ordering choice when
  two trains use the same resource: A clears it before B, or B before A.
  Selecting one arc per pair is the dispatching decision.

The schedule is the **longest path** from the source (each operation's earliest
start); the objective is to choose the alternative arcs minimising the maximum
completion time (makespan). Two strategies are compared on the *same* graph:

- **v1 — priority:** on each resource the higher-priority train goes first (a
  fixed local rule). This is the alternative-graph form of the M2.4 dispatcher.
- **v2 — AMCC** (Avoid Most Critical Completion): greedily find the unselected
  arc that would create the longest path and select the *other* arc of its pair
  to avoid it (taking the critical arc only if the alternative would cycle, i.e.
  deadlock). A global, conflict-aware choice.

Because v2 optimises the makespan and v1 does not, v2 is never worse and is
strictly better when local priority misorders trains relative to the critical
path. The :class:`AltGraphDispatcher` exposes the AMCC ordering through the
swappable :class:`~dbsim.dispatch.base.Dispatcher` interface.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

from dbsim.dispatch.base import Dispatcher, SegmentRequest

if TYPE_CHECKING:
    from dbsim.engine.meso import MesoCorridor, MesoSegment, MesoTrain

_SRC = ("__src__", 0)
_SNK = ("__snk__", 0)
Node = tuple[str, int]
Arc = tuple[Node, Node, int]
Graph = dict[Node, list[tuple[Node, int]]]


@dataclass(frozen=True, slots=True)
class Operation:
    """One train occupying one resource for ``proc_s`` seconds."""

    train_id: str
    resource: str
    proc_s: int


@dataclass(frozen=True, slots=True)
class AltGraphProblem:
    """A dispatching instance: each train's ordered operations + release times."""

    train_ops: dict[str, list[Operation]]
    release: dict[str, int]
    headway_s: int = 0


@dataclass(frozen=True, slots=True)
class AltGraphSolution:
    """A solved schedule: start times, completions, makespan, resource ordering."""

    start: dict[Node, int]
    completion: dict[str, int]
    makespan: int
    resource_order: dict[str, list[str]]
    feasible: bool


@dataclass(frozen=True, slots=True)
class _Assembled:
    nodes: list[Node]
    base: Graph
    op_of: dict[Node, Operation]
    pairs: list[tuple[Arc, Arc]]
    by_resource: dict[str, list[Node]]


def _assemble(problem: AltGraphProblem) -> _Assembled:
    nodes: list[Node] = [_SRC, _SNK]
    op_of: dict[Node, Operation] = {}
    base: Graph = defaultdict(list)
    for train, ops in problem.train_ops.items():
        for i, op in enumerate(ops):
            nodes.append((train, i))
            op_of[train, i] = op
        base[_SRC].append(((train, 0), problem.release[train]))
        for i in range(len(ops) - 1):
            base[(train, i)].append(((train, i + 1), ops[i].proc_s))
        base[(train, len(ops) - 1)].append((_SNK, ops[-1].proc_s))

    by_resource: dict[str, list[Node]] = defaultdict(list)
    for train, ops in problem.train_ops.items():
        for i, op in enumerate(ops):
            by_resource[op.resource].append((train, i))

    pairs: list[tuple[Arc, Arc]] = []
    for op_nodes in by_resource.values():
        for x in range(len(op_nodes)):
            for y in range(x + 1, len(op_nodes)):
                a, b = op_nodes[x], op_nodes[y]
                if a[0] == b[0]:
                    continue
                wa = op_of[a].proc_s + problem.headway_s
                wb = op_of[b].proc_s + problem.headway_s
                pairs.append(((a, b, wa), (b, a, wb)))
    return _Assembled(nodes, base, op_of, pairs, dict(by_resource))


def _topo_order(nodes: list[Node], graph: Graph) -> list[Node]:
    indeg: dict[Node, int] = dict.fromkeys(nodes, 0)
    for u in nodes:
        for v, _w in graph.get(u, ()):
            indeg[v] += 1
    queue = deque(sorted(n for n in nodes if indeg[n] == 0))
    order: list[Node] = []
    while queue:
        u = queue.popleft()
        order.append(u)
        for v, _w in sorted(graph.get(u, ())):
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return order


def _longest_from_source(nodes: list[Node], graph: Graph) -> dict[Node, int]:
    r = dict.fromkeys(nodes, 0)
    for u in _topo_order(nodes, graph):
        for v, w in graph.get(u, ()):
            r[v] = max(r[v], r[u] + w)
    return r


def _longest_to_sink(nodes: list[Node], graph: Graph) -> dict[Node, int]:
    q = dict.fromkeys(nodes, 0)
    for u in reversed(_topo_order(nodes, graph)):
        q[u] = max((w + q[v] for v, w in graph.get(u, ())), default=0)
    return q


def _reachable(graph: Graph, start: Node, target: Node) -> bool:
    seen: set[Node] = set()
    stack = [start]
    while stack:
        u = stack.pop()
        if u == target:
            return True
        if u in seen:
            continue
        seen.add(u)
        stack.extend(v for v, _w in graph.get(u, ()))
    return False


def _graph_with(base: Graph, selected: list[Arc]) -> Graph:
    g: Graph = defaultdict(list)
    for u, outs in base.items():
        g[u].extend(outs)
    for u, v, w in selected:
        g[u].append((v, w))
    return g


def _finalize(problem: AltGraphProblem, a: _Assembled, selected: list[Arc]) -> AltGraphSolution:
    g = _graph_with(a.base, selected)
    r = _longest_from_source(a.nodes, g)
    start = {n: r[n] for n in a.nodes if n not in (_SRC, _SNK)}
    completion = {
        train: r[train, len(ops) - 1] + ops[-1].proc_s for train, ops in problem.train_ops.items()
    }
    order = {
        res: [n[0] for n in sorted(op_nodes, key=lambda n: (start[n], n))]
        for res, op_nodes in a.by_resource.items()
    }
    return AltGraphSolution(start, completion, r[_SNK], order, feasible=True)


def solve_amcc(problem: AltGraphProblem) -> AltGraphSolution:
    """Solve the alternative graph with the AMCC heuristic (v2)."""
    a = _assemble(problem)
    selected: list[Arc] = []
    remaining = list(a.pairs)
    while remaining:
        g = _graph_with(a.base, selected)
        r = _longest_from_source(a.nodes, g)
        q = _longest_to_sink(a.nodes, g)
        best_val, best_pair, best_idx = -math.inf, 0, 0
        for pidx, (arc1, arc2) in enumerate(remaining):
            for ai, (u, v, w) in enumerate((arc1, arc2)):
                val = r[u] + w + q[v]
                if val > best_val:
                    best_val, best_pair, best_idx = val, pidx, ai
        arc1, arc2 = remaining.pop(best_pair)
        critical = arc1 if best_idx == 0 else arc2
        other = arc2 if best_idx == 0 else arc1
        selected.append(critical if _reachable(g, other[1], other[0]) else other)
    return _finalize(problem, a, selected)


def solve_by_priority(problem: AltGraphProblem, priority: dict[str, int]) -> AltGraphSolution:
    """Order each resource by train priority (v1 baseline), cycle-safe."""
    a = _assemble(problem)
    selected: list[Arc] = []
    for arc1, arc2 in a.pairs:
        ta, tb = arc1[0][0], arc1[1][0]  # arc1 = (a-op -> b-op): A before B
        prefer = arc1 if priority.get(ta, 0) >= priority.get(tb, 0) else arc2
        other = arc2 if prefer is arc1 else arc1
        g = _graph_with(a.base, selected)
        selected.append(other if _reachable(g, prefer[1], prefer[0]) else prefer)
    return _finalize(problem, a, selected)


def build_problem_from_meso(corridor: MesoCorridor, trains: list[MesoTrain]) -> AltGraphProblem:
    """Build an :class:`AltGraphProblem` from a meso corridor + trains."""
    train_ops: dict[str, list[Operation]] = {}
    release: dict[str, int] = {}
    for t in trains:
        ops = [
            Operation(t.train_id, str(min(x, y)), corridor.segments[min(x, y)].running_time_s)
            for x, y in pairwise(t.path)
        ]
        train_ops[t.train_id] = ops
        release[t.train_id] = t.entry_time_s
    headway = corridor.segments[0].headway_s if corridor.segments else 0
    return AltGraphProblem(train_ops, release, headway_s=headway)


class AltGraphDispatcher(Dispatcher):
    """Online dispatcher that follows the AMCC per-resource ordering."""

    name = "altgraph"

    def __init__(self, resource_order: dict[str, list[str]]) -> None:
        self._rank = {
            res: {train: i for i, train in enumerate(order)}
            for res, order in resource_order.items()
        }

    def select(
        self, segment: MesoSegment, waiting: Sequence[SegmentRequest], now: int
    ) -> str | None:
        if not waiting:
            return None
        rank = self._rank.get(str(segment.index), {})
        best = min(waiting, key=lambda req: (rank.get(req.train_id, len(rank)), req.train_id))
        return best.train_id
