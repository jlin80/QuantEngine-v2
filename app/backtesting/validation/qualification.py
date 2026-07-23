"""Strategy Qualification Pipeline (Fase 6, mejora recomendada).

Antes de que una estrategia pueda pasar a Paper Trading debe superar
automáticamente una serie de filtros configurables:

1. Validación técnica (corre sin errores).
2. Backtest mínimo (métricas cuantitativas).
3. Monte Carlo (drawdown y riesgo de ruina acotados).
4. Robustez por tramos de mercado.
5. Comparación contra benchmark.
6. Walk-forward estable (si se exige).
7. Revisión de métricas mínimas.
8. Informe automático con aprobación o rechazo **explicado**.
9. Registro permanente del resultado.

El objetivo es que el sistema evolucione de forma controlada y no despliegue
estrategias sobreajustadas. La regla de oro sigue vigente: aprobar aquí habilita
paper trading, nunca live.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.backtesting.benchmark import BenchmarkEngine
from app.backtesting.decisions import DecisionSource
from app.backtesting.engine import BacktestEngine
from app.backtesting.models import BacktestConfig
from app.backtesting.monte_carlo import MonteCarloSimulator
from app.backtesting.validation.criteria import CriterionCheck, evaluate_metrics
from app.backtesting.walk_forward import WalkForwardReport
from app.config.settings import QualificationCriteriaSettings
from app.market.models import Candle


@dataclass(frozen=True, slots=True)
class QualificationReport:
    """Outcome of running a strategy through the qualification pipeline."""

    strategy: str
    approved: bool
    reasons: list[str]
    metric_checks: list[CriterionCheck]
    statistics: dict[str, Any]
    monte_carlo: dict[str, Any]
    benchmarks_beaten: dict[str, bool]
    robustness_pct: float
    walk_forward: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "strategy": self.strategy,
            "approved": self.approved,
            "reasons": self.reasons,
            "metric_checks": [check.to_dict() for check in self.metric_checks],
            "statistics": self.statistics,
            "monte_carlo": self.monte_carlo,
            "benchmarks_beaten": self.benchmarks_beaten,
            "robustness_pct": round(self.robustness_pct, 4),
            "walk_forward": self.walk_forward,
        }


class StrategyQualificationPipeline:
    """Automated gauntlet that approves or rejects a strategy for paper trading.

    Args:
        engine: Motor de backtest.
        benchmark: Motor de benchmarks.
        monte_carlo: Simulador Monte Carlo.
        criteria: Umbrales mínimos configurados.
        robustness_chunks: Nº de tramos contiguos para la prueba de robustez.
    """

    def __init__(
        self,
        engine: BacktestEngine,
        benchmark: BenchmarkEngine,
        monte_carlo: MonteCarloSimulator,
        criteria: QualificationCriteriaSettings,
        *,
        robustness_chunks: int = 4,
    ) -> None:
        self._engine = engine
        self._benchmark = benchmark
        self._monte_carlo = monte_carlo
        self._criteria = criteria
        self._robustness_chunks = max(2, robustness_chunks)

    def qualify(
        self,
        strategy: str,
        candles: Sequence[Candle],
        decision_source: DecisionSource,
        config: BacktestConfig,
        *,
        walk_forward: WalkForwardReport | None = None,
    ) -> QualificationReport:
        """Run the full qualification gauntlet for a strategy.

        Args:
            strategy: Nombre de la estrategia.
            candles: Serie de velas de validación.
            decision_source: Fuente de decisiones de la estrategia.
            config: Configuración base del backtest.
            walk_forward: Informe walk-forward, si ya se ejecutó.

        Returns:
            El informe de calificación con la decisión explicada.
        """
        reasons: list[str] = []

        # 1-2. Validación técnica implícita: correr el backtest sin excepciones.
        result = self._engine.run(candles, decision_source, config)
        stats = result.statistics

        # 7. Revisión de métricas mínimas.
        metric_checks = evaluate_metrics(stats, self._criteria)
        metrics_ok = all(check.passed for check in metric_checks)
        for check in metric_checks:
            if not check.passed:
                reasons.append(f"✗ {check.detail}")

        # 3. Monte Carlo.
        trade_pnls = [trade.pnl for trade in result.trades]
        mc = self._monte_carlo.run(trade_pnls)
        mc_ok = (
            not self._criteria.require_monte_carlo
            or mc.required_capital_pct <= self._criteria.monte_carlo_max_drawdown_pct
        )
        if not mc_ok:
            reasons.append(
                f"✗ Monte Carlo: drawdown p95 {mc.required_capital_pct:.1f}% "
                f"> {self._criteria.monte_carlo_max_drawdown_pct}%"
            )

        # 5. Comparación contra benchmark.
        beaten = self._benchmark.beaten_by(result.return_pct, candles)
        beat_ok = not self._criteria.require_beat_benchmark or (
            beaten.get("buy_and_hold", False) and beaten.get("random", False)
        )
        if not beat_ok:
            reasons.append("✗ No supera a los benchmarks básicos (buy&hold / random)")

        # 4. Robustez por tramos de mercado.
        robustness = self._robustness(candles, decision_source, config)
        robust_ok = robustness >= self._criteria.min_robust_scenarios_pct
        if not robust_ok:
            reasons.append(
                f"✗ Robustez {robustness:.0%} de tramos rentables "
                f"(mínimo {self._criteria.min_robust_scenarios_pct:.0%})"
            )

        # 6. Walk-forward estable (si se exige).
        wf_ok = True
        if self._criteria.require_walk_forward:
            if walk_forward is None:
                wf_ok = False
                reasons.append("✗ Walk-forward requerido y no ejecutado")
            elif not walk_forward.is_stable:
                wf_ok = False
                reasons.append("✗ Walk-forward inestable (pocos pliegues OOS positivos)")

        approved = all((metrics_ok, mc_ok, beat_ok, robust_ok, wf_ok))
        if approved:
            reasons.insert(0, "✓ Supera todos los filtros de calificación")
        return QualificationReport(
            strategy=strategy,
            approved=approved,
            reasons=reasons,
            metric_checks=metric_checks,
            statistics=stats,
            monte_carlo=mc.to_dict(),
            benchmarks_beaten=beaten,
            robustness_pct=robustness,
            walk_forward=walk_forward.to_dict() if walk_forward is not None else None,
        )

    def _robustness(
        self, candles: Sequence[Candle], decision_source: DecisionSource, config: BacktestConfig
    ) -> float:
        """Fraction of contiguous market segments that were profitable."""
        n = len(candles)
        if n < self._robustness_chunks:
            return 0.0
        size = n // self._robustness_chunks
        profitable = 0
        segments = 0
        for i in range(self._robustness_chunks):
            start = i * size
            end = n if i == self._robustness_chunks - 1 else (i + 1) * size
            segment = candles[start:end]
            if len(segment) < 2:
                continue
            segments += 1
            result = self._engine.run(segment, decision_source, config)
            if result.return_pct > 0:
                profitable += 1
        return profitable / segments if segments else 0.0
