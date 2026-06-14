"""Strategy-comparison study (M4.4) — the tool's proof of value.

This is the capstone that ties Phase 4 together. It answers one sharp question:

    On a contended single-track corridor, when trains enter late (delays
    calibrated from real GTFS-RT), which dispatching rule best contains the
    resulting **secondary** (congestion) delay — and how close is the fast AMCC
    heuristic to the CP-SAT optimum?

It composes every Phase-4 piece:

- the **priority** rule (v1, M4.1) and the **AMCC** alternative-graph heuristic
  (v2, M4.1) and the **CP-SAT optimal** baseline (M4.2) — three dispatchers on
  the same alternative graph;
- a **Monte Carlo ensemble** (M4.3): each replication samples an independent set
  of entry delays from a calibrated :class:`DelayModel`, builds the dispatching
  instance, and solves it under all three rules.

The metric is **clearance delay** = how much longer than free-running it takes to
clear every train from the corridor: ``makespan`` minus the makespan the same
trains would have with *zero* contention (each running straight through from its
— already disrupted — entry time). Because every strategy sees the identical
release times, the free-running baseline is identical too, so the difference
between strategies is purely the congestion each dispatching rule induces —
isolating dispatching quality from the disruption itself. CP-SAT minimises the
makespan, so the ``optimal`` strategy is a **true lower bound** on clearance
delay; ``priority`` and ``amcc`` can only match or exceed it. The whole
experiment is reproducible from ``base_seed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dbsim.analysis.montecarlo import DelayModel, _percentile
from dbsim.dispatch.altgraph import (
    AltGraphProblem,
    AltGraphSolution,
    build_problem_from_meso,
    solve_amcc,
    solve_by_priority,
)
from dbsim.dispatch.optimal import solve_optimal
from dbsim.engine.meso import MesoCorridor, MesoSegment, MesoTrain
from dbsim.seed import derive_seed, make_rng

#: The dispatching strategies compared, in increasing sophistication.
STRATEGIES: tuple[str, ...] = ("priority", "amcc", "optimal")


@dataclass(frozen=True, slots=True)
class StudyScenario:
    """A contended corridor plus the trains and their priorities."""

    corridor: MesoCorridor
    trains: tuple[MesoTrain, ...]
    priority: dict[str, int]


def default_scenario() -> StudyScenario:
    """A single-track corridor A–B–C–D with three opposing train pairs.

    Two expresses (priority 10) and four regional trains (priority 0) contend for
    three single-track segments — every opposing pair must meet at a station, so
    the dispatching order is the decision that drives secondary delay.
    """
    corridor = MesoCorridor(
        ("A", "B", "C", "D"),
        (
            MesoSegment(0, "A-B", 600, 1, 120),
            MesoSegment(1, "B-C", 600, 1, 120),
            MesoSegment(2, "C-D", 600, 1, 120),
        ),
    )
    east, west = (0, 1, 2, 3), (3, 2, 1, 0)
    trains = (
        MesoTrain("E_EXP", east, entry_time_s=0, priority=10),
        MesoTrain("E_REG1", east, entry_time_s=400, priority=0),
        MesoTrain("E_REG2", east, entry_time_s=900, priority=0),
        MesoTrain("W_EXP", west, entry_time_s=200, priority=10),
        MesoTrain("W_REG1", west, entry_time_s=600, priority=0),
        MesoTrain("W_REG2", west, entry_time_s=1100, priority=0),
    )
    priority = {t.train_id: t.priority for t in trains}
    return StudyScenario(corridor, trains, priority)


def solve_with_strategy(
    problem: AltGraphProblem, strategy: str, priority: dict[str, int]
) -> AltGraphSolution:
    """Solve a dispatching instance with the named strategy."""
    if strategy == "priority":
        return solve_by_priority(problem, priority)
    if strategy == "amcc":
        return solve_amcc(problem)
    if strategy == "optimal":
        # CP-SAT minimises makespan, the basis of our clearance-delay metric, so
        # this strategy is a true lower bound on what any rule can achieve.
        return solve_optimal(problem)
    raise ValueError(f"unknown strategy: {strategy!r}")


def free_run_makespan_s(problem: AltGraphProblem) -> int:
    """Makespan with zero contention: the latest free-running train completion."""
    return max(
        (problem.release[t] + sum(o.proc_s for o in ops) for t, ops in problem.train_ops.items()),
        default=0,
    )


def clearance_delay_s(problem: AltGraphProblem, sol: AltGraphSolution) -> int:
    """Extra time to clear all trains vs free-running: makespan minus the free run."""
    return sol.makespan - free_run_makespan_s(problem)


@dataclass(frozen=True, slots=True)
class StrategyStats:
    """Distribution of clearance delay (s) for one strategy across replications."""

    strategy: str
    delays_s: tuple[int, ...]

    @property
    def mean_s(self) -> float:
        return sum(self.delays_s) / len(self.delays_s) if self.delays_s else 0.0

    def percentile_s(self, q: float) -> float:
        return _percentile(sorted(self.delays_s), q)

    @property
    def max_s(self) -> int:
        return max(self.delays_s, default=0)


@dataclass(frozen=True, slots=True)
class StrategyStudyResult:
    """Per-strategy distributions plus the AMCC-vs-optimal gap distribution."""

    stats: dict[str, StrategyStats]
    #: AMCC clearance delay minus the optimum, per replication (the heuristic gap).
    amcc_gap_s: tuple[int, ...]
    n_reps: int

    def mean_gap_s(self) -> float:
        return sum(self.amcc_gap_s) / len(self.amcc_gap_s) if self.amcc_gap_s else 0.0

    def amcc_optimal_share(self) -> float:
        """Fraction of replications where AMCC matched the optimum exactly."""
        if not self.amcc_gap_s:
            return 0.0
        return sum(1 for g in self.amcc_gap_s if g == 0) / len(self.amcc_gap_s)

    def improvement_over_priority(self, strategy: str) -> float:
        """Mean reduction in clearance delay vs the priority rule (fraction)."""
        base = self.stats["priority"].mean_s
        if base <= 0:
            return 0.0
        return 1 - self.stats[strategy].mean_s / base


def run_strategy_study(
    scenario: StudyScenario,
    model: DelayModel,
    *,
    n_reps: int = 300,
    base_seed: int = 0,
) -> StrategyStudyResult:
    """Run the Monte Carlo strategy comparison and aggregate the distributions."""
    delays: dict[str, list[int]] = {s: [] for s in STRATEGIES}
    amcc_gap: list[int] = []

    for i in range(n_reps):
        rng = make_rng(derive_seed(base_seed, f"study:{i}"))
        # Sample an independent entry delay per train; rebuild the disrupted corridor.
        disrupted = tuple(
            MesoTrain(
                t.train_id,
                t.path,
                entry_time_s=t.entry_time_s + model.sample_one(rng),
                priority=t.priority,
                dwell_s=t.dwell_s,
            )
            for t in scenario.trains
        )
        problem = build_problem_from_meso(scenario.corridor, list(disrupted))

        per_strategy: dict[str, int] = {}
        for strategy in STRATEGIES:
            sol = solve_with_strategy(problem, strategy, scenario.priority)
            sec = clearance_delay_s(problem, sol)
            delays[strategy].append(sec)
            per_strategy[strategy] = sec
        amcc_gap.append(per_strategy["amcc"] - per_strategy["optimal"])

    stats = {s: StrategyStats(s, tuple(delays[s])) for s in STRATEGIES}
    return StrategyStudyResult(stats=stats, amcc_gap_s=tuple(amcc_gap), n_reps=n_reps)


def format_report(result: StrategyStudyResult, *, source: str) -> Sequence[str]:
    """Render the study result as printable lines (shared by CLI and docs)."""
    lines = [
        f"  delay model         calibrated from {source}",
        f"  replications        {result.n_reps:>8,}",
        "",
        f"  {'strategy':<10} {'mean':>8} {'p50':>8} {'p90':>8} {'max':>8}   (clearance delay, min)",
    ]
    for s in STRATEGIES:
        st = result.stats[s]
        lines.append(
            f"  {s:<10} {st.mean_s / 60:>8.1f} {st.percentile_s(0.5) / 60:>8.1f} "
            f"{st.percentile_s(0.9) / 60:>8.1f} {st.max_s / 60:>8.1f}"
        )
    lines.append("")
    lines.append(
        f"  AMCC vs priority:   {result.improvement_over_priority('amcc') * 100:>5.0f}% "
        "less clearance delay on average"
    )
    lines.append(
        f"  optimal vs priority:{result.improvement_over_priority('optimal') * 100:>5.0f}% "
        "less clearance delay on average"
    )
    lines.append(
        f"  AMCC heuristic gap: {result.mean_gap_s() / 60:>5.1f} min mean above optimum; "
        f"matched the optimum on {result.amcc_optimal_share() * 100:.0f}% of days"
    )
    return lines
