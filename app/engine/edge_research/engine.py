"""Edge Research Engine (Bloque 1) — mide la salud del edge de cada estrategia.

El evaluador continuo (Fase 4) responde *cuánto gana esta estrategia*. Este
motor responde una pregunta distinta y más incómoda: *¿sigue ganando lo mismo
que ganaba?*. Un profit factor acumulado de 1.4 puede esconder una estrategia
que dejó de funcionar hace dos semanas, porque el agregado no olvida.

De ahí la forma del módulo: ventana rodante en vez de acumulado, troceada en
bloques, y métricas que miran la *pendiente* (decay, half-life, drift) además
del nivel. No abre ni cierra operaciones y no puede habilitar live trading:
produce evidencia, y quien la use decide.
"""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config.settings import QuantEdgeResearchSettings
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.engine.edge_research.history import EdgeReportHistory
from app.engine.edge_research.metrics import (
    block_expectancies,
    confidence_drift,
    edge_decay,
    edge_persistence,
    expectancy,
    half_life,
    max_drawdown,
    profit_factor,
    sharpe,
    sortino,
    stability_score,
)
from app.engine.edge_research.models import EdgeResearchReport, StrategyEdgeReport
from app.engine.evaluation.outcomes import VirtualOutcome
from app.engine.events.events import EdgeDecayDetected, EdgeReportGenerated
from app.utils.time import utc_now

OutcomesProvider = Callable[[], Iterable[VirtualOutcome]]

# Estados posibles de una estrategia, de mejor a peor.
STATUS_HEALTHY = "healthy"
STATUS_WATCH = "watch"
STATUS_DEGRADING = "degrading"
STATUS_INSUFFICIENT = "insufficient_data"


@dataclass(kw_only=True, slots=True)
class _Point:
    """Una resolución virtual reducida a lo que necesitan las métricas."""

    r_multiple: float
    confidence: float | None
    closed_at: datetime


class EdgeResearchEngine(Service):
    """Compute, publish and persist per-strategy edge health.

    Args:
        settings: Configuración del motor (ventana, bloques, umbrales).
        outcomes_provider: Fuente de resoluciones virtuales por señal. Se
            inyecta como callable —no como el store— para que este motor sirva
            igual sobre el histórico de un backtest que sobre producción.
        history: Histórico append-only de informes.
        bus: Event Bus donde publicar. Opcional: sin él el motor calcula y
            persiste igual, que es lo que necesitan los tests y el backtest.
    """

    def __init__(
        self,
        settings: QuantEdgeResearchSettings,
        outcomes_provider: OutcomesProvider,
        history: EdgeReportHistory,
        bus: EventBus | None = None,
    ) -> None:
        super().__init__("edge_research")
        self._settings = settings
        self._provider = outcomes_provider
        self._history = history
        self._bus = bus
        self._series: dict[str, deque[_Point]] = {}
        self._seen: deque[str] = deque(maxlen=settings.seen_limit)
        self._seen_set: set[str] = set()
        self._last_report: EdgeResearchReport | None = None
        self._last_status: dict[str, str] = {}
        self._cycles = 0
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger("app.engine.edge_research")

    # ------------------------------------------------------------------
    # Ingesta
    # ------------------------------------------------------------------

    def ingest(self) -> int:
        """Pull new virtual outcomes into the rolling per-strategy series.

        Idempotente por ``signal_id``: la fuente es append-only y se relee
        entera en cada ciclo, así que sin esta guarda cada resolución contaría
        tantas veces como ciclos hayan pasado — y la muestra, que es la base de
        todos los umbrales, sería ficticia.

        Returns:
            Cuántas resoluciones nuevas se incorporaron.
        """
        added = 0
        for outcome in self._provider():
            if outcome.signal_id in self._seen_set:
                continue
            self._remember(outcome.signal_id)
            series = self._series.get(outcome.strategy)
            if series is None:
                series = deque(maxlen=self._settings.rolling_window)
                self._series[outcome.strategy] = series
            series.append(
                _Point(
                    r_multiple=outcome.r_multiple,
                    confidence=outcome.confidence,
                    closed_at=outcome.closed_at,
                )
            )
            added += 1
        return added

    def _remember(self, signal_id: str) -> None:
        """Track a seen signal id within the bounded dedupe window."""
        if len(self._seen) == self._seen.maxlen and self._seen.maxlen is not None:
            self._seen_set.discard(self._seen[0])
        self._seen.append(signal_id)
        self._seen_set.add(signal_id)

    # ------------------------------------------------------------------
    # Cálculo
    # ------------------------------------------------------------------

    def analyze(self, strategy: str) -> StrategyEdgeReport:
        """Compute the full edge health report of one strategy.

        Args:
            strategy: Estrategia a evaluar.

        Returns:
            El informe. Con muestra por debajo de ``min_sample`` devuelve
            ``insufficient_data`` con las métricas en ``None``: no se inventa
            un edge a partir de cuatro operaciones.
        """
        points = list(self._series.get(strategy, ()))
        values = [point.r_multiple for point in points]
        sample = len(values)
        if sample < self._settings.min_sample:
            return StrategyEdgeReport(
                strategy=strategy,
                sample=sample,
                blocks=0,
                expectancy_r=None,
                profit_factor=None,
                sharpe=None,
                sortino=None,
                max_drawdown_r=0.0,
                edge_decay=None,
                half_life_trades=None,
                stability_score=None,
                edge_persistence=None,
                confidence_drift=None,
                health_score=None,
                status=STATUS_INSUFFICIENT,
                reasons=(f"muestra {sample} < mínimo {self._settings.min_sample}",),
            )

        blocks = self._settings.blocks
        confidences = [p.confidence for p in points if p.confidence is not None]
        report_expectancy = expectancy(values)
        report_decay = edge_decay(values, blocks)
        report_stability = stability_score(values, blocks)
        report_persistence = edge_persistence(values, blocks)
        health = self._health_score(
            expectancy_r=report_expectancy,
            stability=report_stability,
            persistence=report_persistence,
            decay=report_decay,
        )
        status, reasons = self._classify(
            expectancy_r=report_expectancy,
            stability=report_stability,
            persistence=report_persistence,
            decay=report_decay,
            health=health,
        )
        return StrategyEdgeReport(
            strategy=strategy,
            sample=sample,
            blocks=len(block_expectancies(values, blocks)),
            expectancy_r=report_expectancy,
            profit_factor=profit_factor(values),
            sharpe=sharpe(values),
            sortino=sortino(values),
            max_drawdown_r=max_drawdown(values),
            edge_decay=report_decay,
            half_life_trades=half_life(values, blocks),
            stability_score=report_stability,
            edge_persistence=report_persistence,
            # La confianza sólo se compara consigo misma: si parte de la
            # muestra no la traía (resoluciones anteriores a este bloque), se
            # mide sobre las que sí, no se rellena con un valor inventado.
            confidence_drift=(
                confidence_drift(confidences, blocks)
                if len(confidences) >= self._settings.min_sample
                else None
            ),
            health_score=health,
            status=status,
            reasons=reasons,
        )

    def _health_score(
        self,
        *,
        expectancy_r: float | None,
        stability: float | None,
        persistence: float | None,
        decay: float | None,
    ) -> float | None:
        """Blend the four readable dimensions into a 0-100 summary.

        El resumen existe para ordenar y alertar, no para decidir: cualquier
        consumidor que necesite justificar una acción tiene las métricas
        individuales y ``reasons``. Los pesos son configurables justamente
        porque son un juicio, no una medida.
        """
        weights = self._settings.health_weights
        parts: list[tuple[float, float]] = []
        if expectancy_r is not None:
            # Expectativa mapeada a 0-1 saturando en ±1 R: por encima de 1 R por
            # operación la diferencia ya no cambia ninguna decisión.
            parts.append((weights.expectancy, _clamp01(0.5 + expectancy_r / 2.0)))
        if stability is not None:
            parts.append((weights.stability, _clamp01(stability)))
        if persistence is not None:
            parts.append((weights.persistence, _clamp01(persistence)))
        if decay is not None:
            # Decay ya viene con signo "positivo = malo"; se normaliza contra el
            # umbral configurado, de modo que el umbral vale exactamente 0.5.
            reference = max(self._settings.decay_threshold, 1e-6)
            parts.append((weights.decay, _clamp01(0.5 - decay / (2.0 * reference))))
        total_weight = sum(weight for weight, _ in parts)
        if total_weight <= 0.0:
            return None
        return 100.0 * sum(weight * value for weight, value in parts) / total_weight

    def _classify(
        self,
        *,
        expectancy_r: float | None,
        stability: float | None,
        persistence: float | None,
        decay: float | None,
        health: float | None,
    ) -> tuple[str, tuple[str, ...]]:
        """Turn the metrics into a status plus the reasons that produced it."""
        reasons: list[str] = []
        if decay is not None and decay > self._settings.decay_threshold:
            reasons.append(f"decay {decay:.4f} R/bloque > umbral {self._settings.decay_threshold}")
        if stability is not None and stability < self._settings.min_stability:
            reasons.append(f"estabilidad {stability:.2f} < mínimo {self._settings.min_stability}")
        if persistence is not None and persistence < self._settings.min_persistence:
            reasons.append(
                f"persistencia {persistence:.2f} < mínimo {self._settings.min_persistence}"
            )
        if expectancy_r is not None and expectancy_r <= 0.0:
            reasons.append(f"expectativa {expectancy_r:.4f} R no positiva")

        if len(reasons) >= self._settings.degrading_reasons:
            return STATUS_DEGRADING, tuple(reasons)
        if reasons:
            return STATUS_WATCH, tuple(reasons)
        detail = "sin señales de deterioro" if health is None else f"health {health:.1f}/100"
        return STATUS_HEALTHY, (detail,)

    # ------------------------------------------------------------------
    # Ciclo de investigación
    # ------------------------------------------------------------------

    def generate(self) -> EdgeResearchReport:
        """Run one full research cycle (ingest → analyze → persist).

        No publica: el bus es asíncrono y este método es síncrono a propósito,
        para que el backtesting y los tests puedan usarlo sin event loop.

        Returns:
            El informe generado.
        """
        self.ingest()
        report = EdgeResearchReport(
            generated_at=utc_now(),
            strategies=tuple(self.analyze(name) for name in sorted(self._series)),
        )
        self._history.record(report)
        self._last_report = report
        self._cycles += 1
        return report

    async def run_cycle(self) -> EdgeResearchReport:
        """Generate a report and publish it on the bus.

        Emite ``EdgeDecayDetected`` **sólo en la transición** a un estado peor.
        Reanunciar el mismo deterioro en cada ciclo convertiría la alarma en
        ruido de fondo, que es como se dejan de leer las alarmas.

        Returns:
            El informe generado.
        """
        report = self.generate()
        if self._bus is None:
            return report
        await self._bus.publish(
            EdgeReportGenerated(
                source="edge_research",
                strategies=len(report.strategies),
                degrading=tuple(r.strategy for r in report.degrading),
                generated_at=report.generated_at.isoformat(),
            )
        )
        for entry in report.strategies:
            previous = self._last_status.get(entry.strategy)
            self._last_status[entry.strategy] = entry.status
            if entry.status == STATUS_DEGRADING and previous != STATUS_DEGRADING:
                await self._bus.publish(
                    EdgeDecayDetected(
                        source="edge_research",
                        strategy=entry.strategy,
                        edge_decay=entry.edge_decay or 0.0,
                        half_life_trades=entry.half_life_trades,
                        expectancy_r=entry.expectancy_r,
                        health_score=entry.health_score,
                        reasons="; ".join(entry.reasons),
                    )
                )
        return report

    # ------------------------------------------------------------------
    # Salida (dashboard, ML, Meta Strategy Manager)
    # ------------------------------------------------------------------

    @property
    def history(self) -> EdgeReportHistory:
        """Histórico append-only de informes."""
        return self._history

    def last_report(self) -> EdgeResearchReport | None:
        """Informe del último ciclo (``None`` si aún no corrió ninguno)."""
        return self._last_report

    def report_for(self, strategy: str) -> StrategyEdgeReport | None:
        """Última salud conocida de una estrategia."""
        if self._last_report is None:
            return None
        return self._last_report.by_strategy().get(strategy)

    def factor(self, strategy: str) -> float:
        """Multiplicador 0-1 de confianza en el edge (1.0 = sin objeción).

        Es el hook para el Meta Strategy Manager y el ML. Devuelve **1.0**
        cuando no hay muestra suficiente: sin evidencia, este motor no penaliza
        a nadie. Lo contrario —castigar por defecto— apagaría toda estrategia
        recién promovida antes de que pudiera demostrar nada.

        Args:
            strategy: Estrategia consultada.

        Returns:
            Multiplicador en ``[floor, 1.0]``.
        """
        entry = self.report_for(strategy)
        if entry is None or entry.health_score is None:
            return 1.0
        floor = self._settings.factor_floor
        return round(floor + (1.0 - floor) * _clamp01(entry.health_score / 100.0), 4)

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "cycles": self._cycles,
            "tracked_strategies": len(self._series),
            "samples": {name: len(series) for name, series in sorted(self._series.items())},
            "history": self._history.status(),
            "last_report": None if self._last_report is None else self._last_report.to_dict(),
        }

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Run research cycles on their own cadence, forever."""
        while True:
            await asyncio.sleep(self._settings.cycle_interval_seconds)
            try:
                await self.run_cycle()
            except Exception:  # el motor de investigación jamás debe morir
                self._log.exception("Edge research cycle failed")

    async def _on_start(self) -> None:
        if self._settings.enabled:
            self._task = asyncio.create_task(self._loop(), name="edge-research")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def _clamp01(value: float) -> float:
    """Clamp a value into ``[0, 1]``."""
    return max(0.0, min(1.0, value))
