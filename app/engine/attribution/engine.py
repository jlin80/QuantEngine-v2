"""Edge Attribution Engine (Bloque 2) — por qué ganó o perdió cada operación.

El método, en una frase: se agrupa cada factor en buckets (terciles para los
continuos, la etiqueta observada para los categóricos), se mide la R media de
cada bucket contra la R media global, y esa diferencia —el ``lift``— es lo que
se atribuye a la operación que cayó en ese bucket.

**Lo que este motor no hace, y es importante que no lo pretenda.** No establece
causas. Los factores están correlacionados entre sí (el spread se ensancha
justo cuando la volatilidad sube, el delta acompaña al momentum), así que sus
asociaciones no son aditivas y no forman una descomposición exacta del
resultado. Por eso cada atribución reporta su **residuo**: lo que la suma de
asociaciones no cubre. Un residuo grande significa que la explicación no
explica, y esa es información valiosa — ocultarla convertiría el informe en una
narración que siempre suena convincente.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from typing import Any

from app.config.settings import QuantAttributionSettings
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.engine.attribution.models import (
    AttributionReport,
    FactorContribution,
    FactorEdge,
    FactorSnapshot,
    TradeAttribution,
)
from app.engine.attribution.store import FactorSnapshotStore
from app.engine.events.events import AttributionReportGenerated
from app.execution.models.trades import TradeRecord
from app.utils.time import utc_now

TradesProvider = Callable[[], Sequence[TradeRecord]]

_LOW, _MID, _HIGH = "low", "mid", "high"


class EdgeAttributionEngine(Service):
    """Explain closed trades by the factors present when they were decided.

    Args:
        settings: Configuración del motor (muestra mínima, cadencia, límites).
        trades_provider: Fuente de operaciones cerradas (Trade Journal).
        snapshots: Store de fotos de factores, indexado por ``decision_id``.
        bus: Event Bus donde publicar. Opcional: sin él calcula igual.
    """

    def __init__(
        self,
        settings: QuantAttributionSettings,
        trades_provider: TradesProvider,
        snapshots: FactorSnapshotStore,
        bus: EventBus | None = None,
    ) -> None:
        super().__init__("edge_attribution")
        self._settings = settings
        self._trades = trades_provider
        self._snapshots = snapshots
        self._bus = bus
        self._last_report: AttributionReport | None = None
        self._edges: dict[str, FactorEdge] = {}
        self._cycles = 0
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger("app.engine.attribution")

    # ------------------------------------------------------------------
    # Join
    # ------------------------------------------------------------------

    def _joined(self) -> tuple[list[tuple[TradeRecord, FactorSnapshot]], dict[str, int]]:
        """Pair each closed trade with the factor snapshot of its decision.

        Returns:
            Los pares unidos y el desglose de por qué quedó fuera lo que quedó
            fuera. Cada caso se cuenta por separado: "sin ``decision_id``" es un
            trade adoptado del broker o anterior a la trazabilidad, y "sin foto"
            es una decisión anterior a este bloque o una captura fallida. Son
            problemas distintos y agregarlos escondería cuál de los dos duele.
        """
        index = self._snapshots.index()
        pairs: list[tuple[TradeRecord, FactorSnapshot]] = []
        breakdown = {"total": 0, "without_decision_id": 0, "without_snapshot": 0, "matched": 0}
        trades = list(self._trades())[-self._settings.max_trades :]
        for trade in trades:
            breakdown["total"] += 1
            if not trade.decision_id:
                breakdown["without_decision_id"] += 1
                continue
            snapshot = index.get(trade.decision_id)
            if snapshot is None:
                breakdown["without_snapshot"] += 1
                continue
            breakdown["matched"] += 1
            pairs.append((trade, snapshot))
        return pairs, breakdown

    # ------------------------------------------------------------------
    # Cálculo
    # ------------------------------------------------------------------

    def analyze(self) -> AttributionReport:
        """Measure every factor's association with the outcome.

        Returns:
            El informe agregado. Con muestra insuficiente devuelve el informe
            con ``factors`` vacío y el desglose del join lleno: sin muestra no
            se publica una explicación, pero sí se publica por qué no la hay.
        """
        pairs, breakdown = self._joined()
        if len(pairs) < self._settings.min_sample:
            self._edges = {}
            return AttributionReport(
                generated_at=utc_now(),
                trades=breakdown["total"],
                matched=breakdown["matched"],
                join_breakdown=breakdown,
            )

        baseline = sum(t.r_multiple for t, _ in pairs) / len(pairs)
        edges: dict[str, FactorEdge] = {}
        for factor in self._numeric_factors(pairs):
            edge = self._numeric_edge(factor, pairs, baseline)
            if edge is not None:
                edges[factor] = edge
        for factor in self._settings.label_factors:
            edge = self._label_edge(factor, pairs, baseline)
            if edge is not None:
                edges[factor] = edge
        self._edges = edges
        return AttributionReport(
            generated_at=utc_now(),
            trades=breakdown["total"],
            matched=breakdown["matched"],
            baseline_r=baseline,
            factors=tuple(sorted(edges.values(), key=lambda e: -abs(e.spread_r))),
            join_breakdown=breakdown,
        )

    def _numeric_factors(self, pairs: Sequence[tuple[TradeRecord, FactorSnapshot]]) -> list[str]:
        """Numeric factor names present in the joined sample, ordered."""
        names: set[str] = set()
        for _, snapshot in pairs:
            names.update(snapshot.numeric)
        # `lag_seconds` mide la captura, no el mercado: explicar el resultado
        # con él sería explicar el instrumento en vez del fenómeno.
        names.discard("lag_seconds")
        return sorted(names)

    def _numeric_edge(
        self,
        factor: str,
        pairs: Sequence[tuple[TradeRecord, FactorSnapshot]],
        baseline: float,
    ) -> FactorEdge | None:
        """Bucket a continuous factor into terciles and measure each bucket."""
        observed: list[tuple[float, float]] = []
        for trade, snapshot in pairs:
            value = snapshot.numeric.get(factor)
            if value is not None:
                observed.append((value, trade.r_multiple))
        if len(observed) < self._settings.min_sample:
            return None
        low_cut, high_cut = _terciles([v for v, _ in observed])
        if low_cut is None or high_cut is None:
            return None
        buckets: dict[str, list[float]] = {_LOW: [], _MID: [], _HIGH: []}
        for value, r in observed:
            buckets[_bucket_of(value, low_cut, high_cut)].append(r)
        return _edge_from_buckets(
            factor,
            "numeric",
            buckets,
            baseline,
            self._settings.min_bucket,
            cuts=(low_cut, high_cut),
        )

    def _label_edge(
        self,
        factor: str,
        pairs: Sequence[tuple[TradeRecord, FactorSnapshot]],
        baseline: float,
    ) -> FactorEdge | None:
        """Measure a categorical factor, one bucket per observed label."""
        buckets: dict[str, list[float]] = {}
        for trade, snapshot in pairs:
            label = snapshot.labels.get(factor, "")
            if not label:
                continue
            buckets.setdefault(label, []).append(trade.r_multiple)
        if not buckets:
            return None
        return _edge_from_buckets(factor, "label", buckets, baseline, self._settings.min_bucket)

    # ------------------------------------------------------------------
    # Atribución por operación
    # ------------------------------------------------------------------

    def explain(self, trade_id: str) -> TradeAttribution | None:
        """Explain one closed trade factor by factor.

        Args:
            trade_id: Operación a explicar.

        Returns:
            La atribución, o ``None`` si la operación no existe, no tiene foto
            de factores o no hay agregado con el que compararla todavía.
        """
        if self._last_report is None:
            return None
        index = self._snapshots.index()
        for trade in reversed(list(self._trades())):
            if trade.trade_id != trade_id:
                continue
            snapshot = None if not trade.decision_id else index.get(trade.decision_id)
            if snapshot is None:
                return None
            return self._attribute(trade, snapshot, self._last_report.baseline_r)
        return None

    def explain_recent(self, limit: int = 20) -> list[TradeAttribution]:
        """Explain the most recent explainable trades (newest first).

        Args:
            limit: Cuántas devolver como máximo.

        Returns:
            Las atribuciones disponibles, las más recientes primero.
        """
        if self._last_report is None:
            return []
        index = self._snapshots.index()
        found: list[TradeAttribution] = []
        for trade in reversed(list(self._trades())):
            if len(found) >= max(0, limit):
                break
            snapshot = None if not trade.decision_id else index.get(trade.decision_id)
            if snapshot is None:
                continue
            found.append(self._attribute(trade, snapshot, self._last_report.baseline_r))
        return found

    def _attribute(
        self, trade: TradeRecord, snapshot: FactorSnapshot, baseline: float
    ) -> TradeAttribution:
        """Turn one trade's factor vector into per-factor contributions."""
        contributions: list[FactorContribution] = []
        for factor, edge in self._edges.items():
            if edge.kind == "numeric":
                value = snapshot.numeric.get(factor)
                if value is None:
                    continue
                bucket = _bucket_of(value, edge.low_cut, edge.high_cut)
            else:
                value = None
                bucket = snapshot.labels.get(factor, "")
                if not bucket:
                    continue
            stats = edge.buckets.get(bucket)
            if stats is None:
                continue
            contributions.append(
                FactorContribution(
                    factor=factor,
                    kind=edge.kind,
                    value=value,
                    bucket=bucket,
                    lift_r=stats["lift_r"],
                    sample=int(stats["sample"]),
                )
            )
        contributions.sort(key=lambda c: -abs(c.lift_r))
        return TradeAttribution(
            trade_id=trade.trade_id,
            decision_id=trade.decision_id,
            symbol=trade.symbol,
            strategy=trade.strategy,
            r_multiple=trade.r_multiple,
            pnl=trade.pnl,
            baseline_r=baseline,
            contributions=tuple(contributions),
        )

    # ------------------------------------------------------------------
    # Ciclo
    # ------------------------------------------------------------------

    def generate(self) -> AttributionReport:
        """Run one attribution cycle (sync, usable sin event loop)."""
        report = self.analyze()
        self._last_report = report
        self._cycles += 1
        return report

    async def run_cycle(self) -> AttributionReport:
        """Generate the report and announce it on the bus.

        Returns:
            El informe generado.
        """
        report = self.generate()
        if self._bus is not None:
            await self._bus.publish(
                AttributionReportGenerated(
                    source="edge_attribution",
                    trades=report.trades,
                    matched=report.matched,
                    factors=len(report.factors),
                    top_factor=report.factors[0].factor if report.factors else "",
                )
            )
        return report

    def last_report(self) -> AttributionReport | None:
        """Informe del último ciclo (``None`` si aún no corrió ninguno)."""
        return self._last_report

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "cycles": self._cycles,
            "factors": len(self._edges),
            "snapshots": self._snapshots.status(),
            "last_report": None if self._last_report is None else self._last_report.to_dict(),
        }

    async def _loop(self) -> None:
        """Run attribution cycles on their own cadence, forever."""
        while True:
            await asyncio.sleep(self._settings.cycle_interval_seconds)
            try:
                await self.run_cycle()
            except Exception:  # la atribución jamás debe morir
                self._log.exception("Attribution cycle failed")

    async def _on_start(self) -> None:
        if self._settings.enabled:
            self._task = asyncio.create_task(self._loop(), name="edge-attribution")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


# ---------------------------------------------------------------------------
# Utilidades de bucketing
# ---------------------------------------------------------------------------


def _terciles(values: Sequence[float]) -> tuple[float | None, float | None]:
    """Tercile cut points of a sample.

    Args:
        values: Muestra del factor.

    Returns:
        ``(corte bajo, corte alto)``, o ``(None, None)`` si el factor es
        constante — un factor sin variación no discrimina nada, y trocearlo
        produciría buckets vacíos con lift 0 que ensucian el informe.
    """
    if len(values) < 3:
        return None, None
    ordered = sorted(values)
    low = ordered[len(ordered) // 3]
    high = ordered[2 * len(ordered) // 3]
    if low == high:
        return None, None
    return low, high


def _bucket_of(value: float, low_cut: float | None, high_cut: float | None) -> str:
    """Name the tercile a value falls into."""
    if low_cut is None or high_cut is None:
        return _MID
    if value <= low_cut:
        return _LOW
    if value >= high_cut:
        return _HIGH
    return _MID


def _edge_from_buckets(
    factor: str,
    kind: str,
    buckets: dict[str, list[float]],
    baseline: float,
    min_bucket: int,
    *,
    cuts: tuple[float | None, float | None] = (None, None),
) -> FactorEdge | None:
    """Summarise per-bucket outcomes into a :class:`FactorEdge`.

    Los buckets por debajo de ``min_bucket`` se descartan: una etiqueta con dos
    operaciones produce un lift enorme y sin significado, y en un dashboard esa
    fila sube arriba del todo justo por ser ruido.
    """
    stats: dict[str, dict[str, float]] = {}
    for name, values in buckets.items():
        if len(values) < min_bucket:
            continue
        mean_r = sum(values) / len(values)
        stats[name] = {
            "sample": float(len(values)),
            "mean_r": round(mean_r, 4),
            "win_rate": round(sum(1 for v in values if v > 0) / len(values), 4),
            "lift_r": round(mean_r - baseline, 4),
        }
    if len(stats) < 2:
        return None
    means = [s["mean_r"] for s in stats.values()]
    return FactorEdge(
        factor=factor,
        kind=kind,
        buckets=stats,
        spread_r=max(means) - min(means),
        sample=int(sum(s["sample"] for s in stats.values())),
        low_cut=cuts[0],
        high_cut=cuts[1],
    )
