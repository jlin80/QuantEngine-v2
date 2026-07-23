"""Optimizador multiobjetivo, Bayesian Lab (TPE) y Simulation Cluster (Fase 10)."""

import asyncio

from app.backtesting.optimizer.space import ParameterSpace
from app.config.settings import (
    BayesianLabSettings,
    MultiObjectiveSettings,
    SimulationClusterSettings,
)
from app.research.bayesian_lab import BayesianLab, TPEOptimizer
from app.research.genetic_optimizer import (
    MultiObjectiveOptimizer,
    dominates,
    scalarize,
)
from app.research.simulation_cluster import SimulationCluster


def _peak_metrics(params):
    peak = -abs(params["fast"] - 10) / 10 - abs(params["slow"] - 40) / 40
    return {
        "profit_factor": 1.5 + peak,
        "sharpe": 1.0 + peak,
        "expectancy_r": 0.2 + peak * 0.1,
        "sqn": 2.0 + peak,
        "max_drawdown_pct": 10.0 - peak * 5,
    }


def test_scalarize_normalizes_percent_and_signs():
    objectives = {"sharpe": 1.0, "max_drawdown_pct": -0.5}
    score = scalarize({"sharpe": 2.0, "max_drawdown_pct": 10.0}, objectives)
    assert round(score, 6) == round(2.0 - 0.5 * 0.1, 6)  # drawdown normalizado /100


def test_pareto_dominance():
    objectives = {"sharpe": 1.0, "max_drawdown_pct": -1.0}
    a = {"sharpe": 2.0, "max_drawdown_pct": 5.0}
    b = {"sharpe": 1.0, "max_drawdown_pct": 8.0}
    assert dominates(a, b, objectives)
    assert not dominates(b, a, objectives)


def test_multi_objective_finds_peak_and_pareto_front():
    space = ParameterSpace().add_range("fast", 3, 20, is_int=True)
    space.add_range("slow", 21, 80, is_int=True)
    settings = MultiObjectiveSettings(population_size=16, generations=8, random_seed=1)
    result = MultiObjectiveOptimizer(settings).optimize(space, _peak_metrics)
    assert result.evaluations > 0
    assert abs(result.best_params["fast"] - 10) <= 3
    assert len(result.pareto_front) >= 1
    assert result.best_score == max(p.score for p in result.pareto_front)


def test_tpe_converges_to_optimum():
    space = ParameterSpace().add_range("x", 0.0, 20.0)
    settings = BayesianLabSettings(max_evaluations=50, n_startup=10, random_seed=1)
    result = TPEOptimizer(settings).optimize(space, lambda p: -((p["x"] - 7.0) ** 2))
    assert result.evaluations == 50
    assert abs(result.best_params["x"] - 7.0) < 0.5


def test_bayesian_lab_records_history_and_compares(tmp_path):
    space = ParameterSpace().add_range("x", 0.0, 20.0)
    lab = BayesianLab(BayesianLabSettings(max_evaluations=30, n_startup=8), history_dir=tmp_path)
    lab.optimize(space, lambda p: -((p["x"] - 5.0) ** 2), label="run1")
    lab.optimize(space, lambda p: -((p["x"] - 5.0) ** 2), label="run2")
    comparison = lab.history.compare()
    assert comparison["count"] == 2
    assert (tmp_path / "history.jsonl").exists()
    # el historial persiste y se recarga
    reloaded = BayesianLab(BayesianLabSettings(), history_dir=tmp_path)
    assert reloaded.history.count() == 2


def test_simulation_cluster_preserves_order():
    cluster = SimulationCluster(SimulationClusterSettings(max_workers=3))
    jobs = [(lambda n=n: n * n) for n in range(6)]
    assert asyncio.run(cluster.run(jobs)) == [0, 1, 4, 9, 16, 25]
    assert cluster.run_sync(jobs) == [0, 1, 4, 9, 16, 25]
    assert asyncio.run(cluster.run([])) == []
