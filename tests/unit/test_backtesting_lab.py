"""Optimizador, benchmarks, walk-forward, calificación, experimentos y fachada (Fase 6)."""

from pathlib import Path
from typing import Any

import pytest
from app.backtesting.api import BacktestLab
from app.backtesting.benchmark import BenchmarkEngine, BuyAndHold
from app.backtesting.datasets import DatasetManager
from app.backtesting.decisions import MovingAverageCrossSource
from app.backtesting.engine import BacktestEngine
from app.backtesting.experiments import ExperimentManager
from app.backtesting.models import BacktestConfig
from app.backtesting.monte_carlo import MonteCarloSimulator
from app.backtesting.optimizer import (
    GeneticOptimizer,
    GridSearch,
    OptimizerNotAvailableError,
    ParameterSpace,
    RandomSearch,
    build_optimizer,
)
from app.backtesting.replay import ReplayController, ReplayState
from app.backtesting.reports import ReportError, ReportGenerator
from app.backtesting.validation import StrategyQualificationPipeline
from app.backtesting.versioning import VersionStore
from app.backtesting.walk_forward import WalkForwardAnalysis
from app.config.settings import (
    BacktestingSettings,
    ExecutionSettings,
    MonteCarloSettings,
    QualificationCriteriaSettings,
    Settings,
)
from app.market.models import Candle


def _config() -> BacktestConfig:
    return BacktestConfig(
        symbol="BTCUSDT", timeframe="1m", initial_balance=10_000.0, spread_bps=2.0
    )


def _candles(count: int = 400, seed: int = 3) -> list[Candle]:
    return DatasetManager().synthetic("BTCUSDT", "1m", count=count, volatility=0.004, seed=seed)


def _ma_factory(params: dict[str, Any]) -> MovingAverageCrossSource:
    return MovingAverageCrossSource(fast=int(params["fast"]), slow=int(params["slow"]))


# --------------------------------------------------------------------------
# Optimizador
# --------------------------------------------------------------------------


def test_grid_search_finds_the_peak():
    space = ParameterSpace().add_choices("x", [1, 2, 3]).add_choices("y", [1, 2])
    result = GridSearch(objective="score").optimize(
        space, lambda p: -((p["x"] - 2) ** 2) - (p["y"] - 1) ** 2
    )
    assert result.best_params == {"x": 2, "y": 1}
    assert result.evaluations == 6


def test_random_search_explores_range():
    space = ParameterSpace().add_range("x", -5.0, 5.0)
    result = RandomSearch(max_evaluations=200, seed=1).optimize(
        space, lambda p: -((p["x"] - 1.5) ** 2)
    )
    assert result.best_score > -0.5  # se acerca al óptimo x=1.5


def test_genetic_optimizer_converges():
    space = ParameterSpace().add_range("x", -5.0, 5.0).add_range("y", -5.0, 5.0)
    result = GeneticOptimizer(population_size=16, generations=12, seed=2).optimize(
        space, lambda p: -((p["x"] - 1.0) ** 2) - (p["y"] + 2.0) ** 2
    )
    assert result.best_score > -1.0


def test_prepared_optimizers_raise():
    space = ParameterSpace().add_choices("x", [1, 2])
    for method in ("bayesian", "optuna"):
        with pytest.raises(OptimizerNotAvailableError):
            build_optimizer(method).optimize(space, lambda p: float(p["x"]))


def test_grid_rejects_continuous_range():
    space = ParameterSpace().add_range("x", 0.0, 1.0)
    with pytest.raises(ValueError, match="discretos"):
        GridSearch().optimize(space, lambda p: p["x"])


# --------------------------------------------------------------------------
# Benchmarks
# --------------------------------------------------------------------------


def test_buy_and_hold_matches_close_to_close():
    candles = _candles(count=120, seed=9)
    result = BuyAndHold().evaluate(candles)
    expected = (candles[-1].close / candles[0].close - 1.0) * 100.0
    assert result.return_pct == pytest.approx(expected, rel=1e-6)


def test_benchmark_engine_reports_all_four():
    results = BenchmarkEngine().evaluate(_candles(count=120, seed=4))
    assert set(results) == {"buy_and_hold", "random", "ema_cross", "vwap_basic"}


# --------------------------------------------------------------------------
# Walk-forward
# --------------------------------------------------------------------------


def test_walk_forward_analysis_runs():
    candles = _candles(count=500, seed=6)
    engine = BacktestEngine(BacktestingSettings(), ExecutionSettings(enabled=True))
    space = ParameterSpace().add_choices("fast", [3, 5]).add_choices("slow", [20])
    analysis = WalkForwardAnalysis(engine, GridSearch(objective="sharpe"), space, _ma_factory)
    report = analysis.run(
        candles, _config(), scheme="rolling", train_size=150, validation_size=50, step=75
    )
    assert report.folds  # generó al menos un pliegue
    assert "positive_fold_ratio" in report.to_dict()


# --------------------------------------------------------------------------
# Calificación
# --------------------------------------------------------------------------


def _pipeline(criteria: QualificationCriteriaSettings) -> StrategyQualificationPipeline:
    engine = BacktestEngine(BacktestingSettings(), ExecutionSettings(enabled=True))
    return StrategyQualificationPipeline(
        engine,
        BenchmarkEngine(),
        MonteCarloSimulator(MonteCarloSettings(simulations=100)),
        criteria,
        robustness_chunks=3,
    )


def test_qualification_rejects_with_reasons():
    candles = _candles(count=250, seed=3)
    report = _pipeline(QualificationCriteriaSettings()).qualify(
        "ma_cross", candles, MovingAverageCrossSource(fast=5, slow=20), _config()
    )
    assert report.approved is False
    assert report.reasons
    assert "statistics" in report.to_dict()


def test_qualification_approves_when_criteria_lenient():
    candles = _candles(count=250, seed=3)
    lenient = QualificationCriteriaSettings(
        min_trades=1,
        min_profit_factor=0.0,
        min_sharpe=-100.0,
        max_drawdown_pct=100.0,
        min_expectancy=-1_000.0,
        min_sqn=-100.0,
        require_walk_forward=False,
        require_monte_carlo=False,
        require_beat_benchmark=False,
        min_robust_scenarios_pct=0.0,
    )
    report = _pipeline(lenient).qualify(
        "ma_cross", candles, MovingAverageCrossSource(fast=5, slow=20), _config()
    )
    assert report.approved is True


# --------------------------------------------------------------------------
# Experimentos y versionado
# --------------------------------------------------------------------------


def test_experiments_are_append_only(tmp_path):
    manager = ExperimentManager(tmp_path / "exp")
    manager.save(label="a", result={"return_pct": 1.0})
    manager.save(label="b", result={"return_pct": 2.0})
    assert manager.count() == 2
    labels = {exp.label for exp in manager.all()}
    assert labels == {"a", "b"}


def test_versioning_increments_and_rolls_back(tmp_path):
    store = VersionStore(tmp_path / "ver")
    v1 = store.add("ma_cross", {"fast": 5}, notes="inicial")
    v2 = store.add("ma_cross", {"fast": 8})
    assert v1.version == "1.0"
    assert v2.version == "1.1"
    latest = store.latest("ma_cross")
    assert latest is not None and latest.version == "1.1"
    first = store.get("ma_cross", "1.0")
    assert first is not None and first.parameters == {"fast": 5}
    rolled = store.rollback("ma_cross", "1.0")
    assert rolled.version == "1.2"
    assert rolled.parameters == {"fast": 5}


# --------------------------------------------------------------------------
# Reportes y replay
# --------------------------------------------------------------------------


def test_reports_render_all_text_formats(tmp_path):
    engine = BacktestEngine(BacktestingSettings(), ExecutionSettings(enabled=True))
    result = engine.run(_candles(count=120, seed=7), MovingAverageCrossSource(), _config())
    gen = ReportGenerator()
    assert "Backtest" in gen.to_markdown(result)
    assert "<svg" in gen.to_html(result) or result.bars >= 2
    written = gen.save(result, tmp_path, formats=("json", "md", "html"))
    assert all(path.exists() for path in written.values())
    with pytest.raises(ReportError):
        gen.to_pdf(result)


def test_replay_controller_navigation():
    candles = _candles(count=10, seed=1)
    controller = ReplayController(candles=candles)
    assert controller.step_forward() is candles[0]
    controller.step_forward()
    assert controller.cursor == 1
    controller.step_back()
    assert controller.cursor == 0
    controller.seek(9)
    assert controller.state is ReplayState.FINISHED


# --------------------------------------------------------------------------
# Fachada BacktestLab
# --------------------------------------------------------------------------


def _lab(tmp_path: Path) -> BacktestLab:
    settings = Settings()
    bt = settings.backtesting.model_copy(
        update={
            "experiments_dir": tmp_path / "exp",
            "versions_dir": tmp_path / "ver",
            "results_dir": tmp_path / "res",
        }
    )
    execution = settings.execution.model_copy(update={"enabled": True})
    return BacktestLab(settings.model_copy(update={"backtesting": bt, "execution": execution}))


def test_lab_facade_end_to_end(tmp_path):
    lab = _lab(tmp_path)
    candles = _candles(count=300, seed=3)
    config = lab.make_config("BTCUSDT", label="facade")
    result = lab.run_backtest(candles, MovingAverageCrossSource(), config)
    assert result.bars == 300

    stats = lab.calculate_statistics(result.trades, result.equity_curve)
    assert "sqn" in stats

    mc = lab.run_monte_carlo([t.pnl for t in result.trades] or [1.0, -1.0])
    assert mc.simulations > 0

    space = ParameterSpace().add_choices("fast", [3, 5]).add_choices("slow", [20])
    optimization = lab.optimize_parameters(candles, space, _ma_factory, config, method="grid")
    assert optimization.best_params

    written = lab.generate_report(result, formats=("json",))
    assert written["json"].exists()

    experiment = lab.save_experiment("facade", result.to_dict(include_trades=False))
    assert lab.experiments.count() == 1
    assert experiment.label == "facade"


def test_lab_load_dataset_roundtrip(tmp_path):
    lab = _lab(tmp_path)
    candles = _candles(count=20, seed=2)
    csv = tmp_path / "data.csv"
    csv.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "\n".join(
            f"{c.start.isoformat()},{c.open},{c.high},{c.low},{c.close},{c.volume}" for c in candles
        ),
        encoding="utf-8",
    )
    loaded = lab.load_dataset(csv, "BTCUSDT", "1m")
    assert len(loaded) == 20
