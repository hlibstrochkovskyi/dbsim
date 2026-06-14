"""Tests for the strategy-comparison study (M4.4)."""

from __future__ import annotations

from dbsim.analysis.montecarlo import DelayModel
from dbsim.analysis.strategy_study import (
    STRATEGIES,
    clearance_delay_s,
    default_scenario,
    run_strategy_study,
    solve_with_strategy,
)
from dbsim.dispatch.altgraph import build_problem_from_meso


def _model() -> DelayModel:
    return DelayModel(p_delayed=0.4, magnitudes_s=(300, 600, 900, 1200))


def test_optimal_never_worse_than_heuristics_each_rep() -> None:
    # On every replication the optimum is a true lower bound on clearance delay.
    scenario = default_scenario()
    result = run_strategy_study(scenario, _model(), n_reps=80, base_seed=0)
    opt = result.stats["optimal"]
    for strategy in ("priority", "amcc"):
        st = result.stats[strategy]
        assert all(s >= o for s, o in zip(st.delays_s, opt.delays_s, strict=True))


def test_amcc_gap_is_non_negative() -> None:
    result = run_strategy_study(default_scenario(), _model(), n_reps=80, base_seed=0)
    assert all(g >= 0 for g in result.amcc_gap_s)
    assert result.mean_gap_s() >= 0


def test_smart_dispatching_beats_priority_on_average() -> None:
    result = run_strategy_study(default_scenario(), _model(), n_reps=150, base_seed=0)
    # The contended corridor makes the rigid priority rule strictly worse.
    assert result.improvement_over_priority("amcc") > 0.0
    assert result.improvement_over_priority("optimal") >= result.improvement_over_priority("amcc")


def test_study_is_reproducible_from_base_seed() -> None:
    r1 = run_strategy_study(default_scenario(), _model(), n_reps=60, base_seed=0)
    r2 = run_strategy_study(default_scenario(), _model(), n_reps=60, base_seed=0)
    for s in STRATEGIES:
        assert r1.stats[s].delays_s == r2.stats[s].delays_s
    assert r1.amcc_gap_s == r2.amcc_gap_s


def test_clearance_delay_is_zero_for_an_uncontended_single_train() -> None:
    scenario = default_scenario()
    one = scenario.trains[0]
    problem = build_problem_from_meso(scenario.corridor, [one])
    for strategy in STRATEGIES:
        sol = solve_with_strategy(problem, strategy, scenario.priority)
        assert clearance_delay_s(problem, sol) == 0  # nothing to contend with


def test_unknown_strategy_raises() -> None:
    scenario = default_scenario()
    problem = build_problem_from_meso(scenario.corridor, list(scenario.trains))
    try:
        solve_with_strategy(problem, "bogus", scenario.priority)
    except ValueError as e:
        assert "bogus" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
