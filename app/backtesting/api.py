"""BacktestLab: fachada de las APIs internas del laboratorio (Fase 6).

Punto único de entrada para el dashboard, los scripts y las fases futuras.
Expone las funciones que pide la especificación —``run_backtest``,
``run_walk_forward``, ``run_monte_carlo``, ``optimize_parameters``,
``compare_versions``, ``calculate_statistics``, ``generate_report``,
``save_experiment``, ``load_dataset``, ``replay_market``— sobre los motores
desacoplados de este paquete. Refleja el patrón de ``QuantCore`` y
``ExecutionCore``. Ninguna operación habilita live trading.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.backtesting.benchmark import BenchmarkEngine
from app.backtesting.datasets import DatasetManager
from app.backtesting.decisions import DecisionSource
from app.backtesting.engine import BacktestEngine
from app.backtesting.experiments import Experiment, ExperimentManager
from app.backtesting.metrics import StatisticsEngine
from app.backtesting.models import BacktestConfig, BacktestResult, EquityPoint
from app.backtesting.monte_carlo import MonteCarloResult, MonteCarloSimulator
from app.backtesting.optimizer import OptimizationResult, ParameterSpace, build_optimizer
from app.backtesting.replay import ReplayController
from app.backtesting.reports import ReportGenerator
from app.backtesting.validation import (
    QualificationReport,
    StrategyQualificationPipeline,
)
from app.backtesting.versioning import VersionStore
from app.backtesting.walk_forward import (
    SourceFactory,
    WalkForwardAnalysis,
    WalkForwardReport,
    objective_value,
)
from app.config.settings import Settings
from app.market.models import Candle


class BacktestLab:
    """Facade over the quantitative laboratory.

    Args:
        settings: Configuración raíz del sistema (usa ``backtesting`` y
            ``execution``).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bt = settings.backtesting
        self._execution = settings.execution
        self._engine = BacktestEngine(self._bt, self._execution)
        self._datasets = DatasetManager()
        self._reports = ReportGenerator()
        self._benchmark = BenchmarkEngine()
        self._experiments = ExperimentManager(self._bt.experiments_dir)
        self._versions = VersionStore(self._bt.versions_dir)

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------

    def load_dataset(
        self, path: Path | str, symbol: str, timeframe: str | None = None
    ) -> list[Candle]:
        """Load a historical dataset into candles."""
        return self._datasets.load(path, symbol, timeframe or self._bt.default_timeframe)

    def make_config(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        label: str = "backtest",
        **overrides: Any,
    ) -> BacktestConfig:
        """Build a :class:`BacktestConfig` from lab defaults plus overrides."""
        return BacktestConfig(
            symbol=symbol,
            timeframe=timeframe or self._bt.default_timeframe,
            initial_balance=overrides.get("initial_balance", self._bt.initial_balance),
            spread_bps=overrides.get("spread_bps", self._bt.default_spread_bps),
            label=label,
            parameters=overrides.get("parameters", {}),
        )

    # ------------------------------------------------------------------
    # Backtesting y estadística
    # ------------------------------------------------------------------

    def run_backtest(
        self, candles: Sequence[Candle], decision_source: DecisionSource, config: BacktestConfig
    ) -> BacktestResult:
        """Run a single backtest over the candles."""
        return self._engine.run(candles, decision_source, config)

    def calculate_statistics(
        self,
        trades: Sequence[Any],
        equity_curve: Sequence[EquityPoint] | None = None,
        *,
        initial_balance: float | None = None,
    ) -> dict[str, Any]:
        """Compute the full quant statistics for a set of trades."""
        balance = initial_balance if initial_balance is not None else self._bt.initial_balance
        engine = StatisticsEngine(balance, risk_free_rate=self._bt.risk_free_rate)
        return engine.compute(trades, equity_curve).to_dict()

    # ------------------------------------------------------------------
    # Optimización y validación temporal
    # ------------------------------------------------------------------

    def optimize_parameters(
        self,
        candles: Sequence[Candle],
        space: ParameterSpace,
        source_factory: SourceFactory,
        config: BacktestConfig,
        *,
        method: str | None = None,
        objective: str | None = None,
    ) -> OptimizationResult:
        """Optimize strategy parameters over the candles.

        Args:
            candles: Serie de velas.
            space: Espacio de parámetros.
            source_factory: Construye una fuente de decisiones desde un parámetro.
            config: Configuración base del backtest.
            method: Método de optimización (por defecto el de settings).
            objective: Métrica a maximizar (por defecto la de settings).

        Returns:
            El resultado de la optimización.
        """
        opt = self._settings.backtesting.optimizer
        objective = objective or opt.objective
        optimizer = build_optimizer(
            method or opt.method,
            objective=objective,
            max_evaluations=opt.max_evaluations,
            seed=opt.random_seed,
            population_size=opt.population_size,
            generations=opt.generations,
            mutation_rate=opt.mutation_rate,
        )

        def evaluate(params: dict[str, Any]) -> float:
            result = self._engine.run(candles, source_factory(params), config)
            return objective_value(result.statistics, objective)

        return optimizer.optimize(space, evaluate)

    def run_walk_forward(
        self,
        candles: Sequence[Candle],
        space: ParameterSpace,
        source_factory: SourceFactory,
        config: BacktestConfig,
        *,
        method: str | None = None,
        objective: str | None = None,
    ) -> WalkForwardReport:
        """Run walk-forward optimization and out-of-sample validation."""
        opt = self._settings.backtesting.optimizer
        wf = self._settings.backtesting.walk_forward
        objective = objective or opt.objective
        optimizer = build_optimizer(
            method or opt.method,
            objective=objective,
            max_evaluations=opt.max_evaluations,
            seed=opt.random_seed,
            population_size=opt.population_size,
            generations=opt.generations,
            mutation_rate=opt.mutation_rate,
        )
        analysis = WalkForwardAnalysis(
            self._engine, optimizer, space, source_factory, objective=objective
        )
        return analysis.run(
            candles,
            config,
            scheme=wf.scheme,
            train_size=wf.train_size,
            validation_size=wf.validation_size,
            step=wf.step,
        )

    def run_monte_carlo(self, trade_pnls: Sequence[float]) -> MonteCarloResult:
        """Run a Monte Carlo study over a sequence of trade PnLs."""
        return MonteCarloSimulator(self._settings.backtesting.monte_carlo).run(trade_pnls)

    # ------------------------------------------------------------------
    # Calificación, comparación y reportes
    # ------------------------------------------------------------------

    def qualification_pipeline(self) -> StrategyQualificationPipeline:
        """Build the strategy qualification pipeline with lab components."""
        return StrategyQualificationPipeline(
            self._engine,
            self._benchmark,
            MonteCarloSimulator(self._settings.backtesting.monte_carlo),
            self._settings.backtesting.criteria,
        )

    def qualify_strategy(
        self,
        strategy: str,
        candles: Sequence[Candle],
        decision_source: DecisionSource,
        config: BacktestConfig,
        *,
        walk_forward: WalkForwardReport | None = None,
    ) -> QualificationReport:
        """Run a strategy through the full qualification gauntlet."""
        return self.qualification_pipeline().qualify(
            strategy, candles, decision_source, config, walk_forward=walk_forward
        )

    def compare_versions(
        self,
        candles: Sequence[Candle],
        source_factory: SourceFactory,
        config: BacktestConfig,
        versions: dict[str, dict[str, Any]],
        *,
        objective: str | None = None,
    ) -> dict[str, Any]:
        """Backtest several parameter versions and rank them.

        Args:
            candles: Serie de velas.
            source_factory: Construye una fuente de decisiones desde parámetros.
            config: Configuración base del backtest.
            versions: Mapa versión → parámetros.
            objective: Métrica de ordenación (por defecto la de settings).

        Returns:
            Diccionario con el resumen por versión y la mejor.
        """
        objective = objective or self._settings.backtesting.optimizer.objective
        summary: dict[str, Any] = {}
        best_version = ""
        best_score = float("-inf")
        for version, params in versions.items():
            result = self._engine.run(candles, source_factory(params), config)
            score = objective_value(result.statistics, objective)
            summary[version] = {
                "parameters": params,
                "return_pct": round(result.return_pct, 4),
                "objective": objective,
                "score": None if score == float("-inf") else round(score, 6),
                "statistics": result.statistics,
            }
            if score > best_score:
                best_score = score
                best_version = version
        return {"objective": objective, "best_version": best_version, "versions": summary}

    def generate_report(
        self,
        result: BacktestResult,
        *,
        formats: Sequence[str] = ("json",),
        directory: Path | str | None = None,
    ) -> dict[str, Path]:
        """Write report files for a backtest result.

        Args:
            result: Resultado a reportar.
            formats: Formatos (``json`` | ``md`` | ``html``).
            directory: Carpeta de salida (por defecto ``results_dir``).

        Returns:
            Mapa formato → ruta escrita.
        """
        out = Path(directory) if directory is not None else self._bt.results_dir
        return self._reports.save(result, out, formats=formats)

    def report_generator(self) -> ReportGenerator:
        """The underlying report generator (for in-memory rendering)."""
        return self._reports

    # ------------------------------------------------------------------
    # Experimentos, versiones y replay
    # ------------------------------------------------------------------

    def save_experiment(self, label: str, result: dict[str, Any], **kwargs: Any) -> Experiment:
        """Persist an experiment record (append-only, never overwrites)."""
        return self._experiments.save(label=label, result=result, **kwargs)

    @property
    def experiments(self) -> ExperimentManager:
        """The experiment manager."""
        return self._experiments

    @property
    def versions(self) -> VersionStore:
        """The parameter-set version store."""
        return self._versions

    @property
    def benchmarks(self) -> BenchmarkEngine:
        """The benchmark engine."""
        return self._benchmark

    def replay_market(self, candles: Sequence[Candle], *, speed: float = 1.0) -> ReplayController:
        """Build a replay controller over a candle series."""
        return ReplayController(candles=candles, speed=speed)

    # ------------------------------------------------------------------
    # Diagnóstico (dashboard)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Compact laboratory status for the dashboard."""
        return {
            "enabled": self._bt.enabled,
            "optimizer_method": self._bt.optimizer.method,
            "objective": self._bt.optimizer.objective,
            "walk_forward_scheme": self._bt.walk_forward.scheme,
            "monte_carlo_simulations": self._bt.monte_carlo.simulations,
            "benchmarks": self._benchmark.names,
            "experiments": self._experiments.count(),
        }

    def criteria(self) -> dict[str, Any]:
        """Qualification thresholds as a JSON-safe dict."""
        return self._bt.criteria.model_dump()
