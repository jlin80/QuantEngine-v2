"""Pipeline de validación de candidatas (Fase 10).

Orquesta las etapas obligatorias sobre los motores públicos de ``BacktestLab``
(Fase 6): un único backtest alimenta la curva de equity y las operaciones, y de
ahí se derivan las puertas de métricas, walk-forward, Monte Carlo, benchmark y
riesgo. La revisión ML es asesora y opcional. Ninguna etapa habilita live.
"""

from collections.abc import Callable, Sequence

from app.backtesting import BacktestLab
from app.backtesting.models import BacktestConfig
from app.backtesting.validation.criteria import all_passed, evaluate_metrics
from app.config.settings import CandidatePipelineSettings, QualificationCriteriaSettings
from app.market.models import Candle
from app.research import stats as st
from app.research.models import (
    CandidateReport,
    CandidateStatus,
    PipelineStage,
    StageResult,
    StrategyGenome,
)
from app.research.parameter_lab import build_source_factory, build_space
from app.research.strategy_generator import compile_genome

# Revisor ML opcional: recibe el genoma y las estadísticas, devuelve (ok, detalle).
MLReviewFn = Callable[[StrategyGenome, dict[str, float]], tuple[bool, str]]


class CandidatePipeline:
    """Full validation gauntlet turning a genome into a candidate.

    Args:
        lab: Laboratorio de backtesting (Fase 6).
        settings: Qué etapas se exigen.
        criteria: Umbrales cuantitativos mínimos.
        objective: Métrica primaria para el score del informe.
        ml_review: Revisor ML asesor opcional.
    """

    def __init__(
        self,
        lab: BacktestLab,
        settings: CandidatePipelineSettings,
        criteria: QualificationCriteriaSettings,
        *,
        objective: str = "sharpe",
        ml_review: MLReviewFn | None = None,
    ) -> None:
        self._lab = lab
        self._settings = settings
        self._criteria = criteria
        self._objective = objective
        self._ml_review = ml_review

    def evaluate(
        self, genome: StrategyGenome, candles: Sequence[Candle], config: BacktestConfig
    ) -> CandidateReport:
        """Run a genome through every required stage.

        Args:
            genome: Genoma a validar (se compila sobre una copia).
            candles: Serie histórica de validación.
            config: Configuración base del backtest.

        Returns:
            El informe de candidata con el resultado por etapa.
        """
        source = compile_genome(genome)
        source.reset()
        result = self._lab.run_backtest(candles, source, config)
        stats: dict[str, float] = {
            k: float(v) for k, v in result.statistics.items() if isinstance(v, (int, float))
        }
        equity = [point.equity for point in result.equity_curve]
        stats["stability"] = round(st.equity_stability(equity), 6)

        stages: list[StageResult] = []
        reasons: list[str] = []

        # 1. Backtesting: métricas cuantitativas mínimas.
        checks = evaluate_metrics(result.statistics, self._criteria)
        bt_ok = all_passed(checks)
        failed = [c.detail for c in checks if not c.passed]
        stages.append(
            StageResult(
                stage=PipelineStage.BACKTEST,
                passed=bt_ok,
                score=stats.get(self._objective),
                detail="métricas mínimas OK" if bt_ok else "; ".join(failed[:3]),
                metrics={
                    k: stats[k]
                    for k in ("profit_factor", "sharpe", "expectancy_r", "total_trades")
                    if k in stats
                },
            )
        )
        if not bt_ok:
            reasons.extend(failed)

        # 2. Walk Forward (out-of-sample estable).
        if self._settings.require_walk_forward:
            stages.append(self._walk_forward_stage(genome, candles, config, reasons))

        # 3. Monte Carlo (drawdown/ruina acotados).
        if self._settings.require_monte_carlo:
            stages.append(self._monte_carlo_stage(result.trades, reasons))

        # 4. Validación ML (asesora, opcional).
        if self._settings.require_ml_review:
            stages.append(self._ml_stage(genome, stats, reasons))

        # 5. Comparación Benchmark.
        if self._settings.require_benchmark:
            stages.append(self._benchmark_stage(result.return_pct, candles, reasons))

        # 6. Risk Review (drawdown y expectativa).
        if self._settings.require_risk_review:
            stages.append(self._risk_stage(stats, reasons))

        passed = all(stage.passed for stage in stages)
        status = CandidateStatus.CANDIDATE if passed else CandidateStatus.REJECTED
        if passed:
            reasons.insert(0, "Supera todas las etapas del pipeline")
        return CandidateReport(
            genome_id=genome.id,
            name=genome.name,
            symbol=genome.symbol,
            status=status,
            passed=passed,
            objective=self._objective,
            score=stats.get(self._objective),
            stages=tuple(stages),
            statistics=stats,
            reasons=tuple(reasons),
        )

    def _walk_forward_stage(
        self,
        genome: StrategyGenome,
        candles: Sequence[Candle],
        config: BacktestConfig,
        reasons: list[str],
    ) -> StageResult:
        """Run walk-forward and judge out-of-sample stability."""
        space = build_space(genome)
        if not space.names:
            return StageResult(
                stage=PipelineStage.WALK_FORWARD,
                passed=True,
                detail="sin parámetros optimizables: walk-forward no aplica",
            )
        factory = build_source_factory(genome)
        report = self._lab.run_walk_forward(
            candles, space, factory, config, method=self._settings.walk_forward_method
        )
        stable = report.is_stable
        if not stable:
            reasons.append("Walk-forward inestable (pocos pliegues OOS positivos)")
        return StageResult(
            stage=PipelineStage.WALK_FORWARD,
            passed=stable,
            detail="estable" if stable else "inestable",
            metrics={"folds": float(len(report.folds))},
        )

    def _monte_carlo_stage(self, trades: Sequence[object], reasons: list[str]) -> StageResult:
        """Bound the drawdown at the 95th percentile via Monte Carlo."""
        pnls = [float(getattr(trade, "pnl", 0.0)) for trade in trades]
        mc = self._lab.run_monte_carlo(pnls)
        cap = self._criteria.monte_carlo_max_drawdown_pct
        ok = mc.required_capital_pct <= cap
        if not ok:
            reasons.append(f"Monte Carlo: drawdown p95 {mc.required_capital_pct:.1f}% > {cap:.1f}%")
        return StageResult(
            stage=PipelineStage.MONTE_CARLO,
            passed=ok,
            detail=f"drawdown p95 {mc.required_capital_pct:.1f}%",
            metrics={"required_capital_pct": mc.required_capital_pct},
        )

    def _ml_stage(
        self, genome: StrategyGenome, stats: dict[str, float], reasons: list[str]
    ) -> StageResult:
        """Advisory ML review (skipped cleanly when no reviewer is wired)."""
        if self._ml_review is None:
            return StageResult(
                stage=PipelineStage.ML_REVIEW,
                passed=True,
                detail="sin revisor ML: etapa asesora omitida",
            )
        ok, detail = self._ml_review(genome, stats)
        if not ok:
            reasons.append(f"Revisión ML: {detail}")
        return StageResult(stage=PipelineStage.ML_REVIEW, passed=ok, detail=detail)

    def _benchmark_stage(
        self, return_pct: float, candles: Sequence[Candle], reasons: list[str]
    ) -> StageResult:
        """Beat the basic benchmarks (buy & hold and random)."""
        beaten = self._lab.benchmarks.beaten_by(return_pct, candles)
        ok = beaten.get("buy_and_hold", False) and beaten.get("random", False)
        if not ok:
            reasons.append("No supera los benchmarks básicos (buy&hold / random)")
        return StageResult(
            stage=PipelineStage.BENCHMARK,
            passed=ok,
            detail="supera buy&hold y random" if ok else "no supera los benchmarks",
            metrics={name: 1.0 if won else 0.0 for name, won in beaten.items()},
        )

    def _risk_stage(self, stats: dict[str, float], reasons: list[str]) -> StageResult:
        """Focused risk gate: drawdown ceiling and positive expectancy."""
        dd = stats.get("max_drawdown_pct", 100.0)
        expectancy = stats.get("expectancy", -1.0)
        dd_ok = dd <= self._criteria.max_drawdown_pct
        exp_ok = expectancy >= self._criteria.min_expectancy
        ok = dd_ok and exp_ok
        if not dd_ok:
            reasons.append(f"Drawdown {dd:.1f}% > {self._criteria.max_drawdown_pct:.1f}%")
        if not exp_ok:
            reasons.append(f"Expectativa {expectancy:.4f} < {self._criteria.min_expectancy:.4f}")
        return StageResult(
            stage=PipelineStage.RISK_REVIEW,
            passed=ok,
            detail="riesgo dentro de límites" if ok else "riesgo fuera de límites",
            metrics={"max_drawdown_pct": dd, "expectancy": expectancy},
        )
