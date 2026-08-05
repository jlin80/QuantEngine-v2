"""Live Shadow Benchmark (Bloque 15) — paper contra live contra el fill ideal.

Compara tres carriles de ejecución para la misma decisión:

- **ideal**: el precio que la decisión asumió, sin coste de ejecución.
- **paper**: lo que el simulador rellenó de verdad.
- **live**: lo que el bróker habría rellenado con dinero real.

**El carril live no existe, y ese es el punto de este módulo.** Live trading está
deshabilitado por regla del proyecto, y este bloque **no lo habilita ni lo
prepara para habilitarse**: se limita a dejar el hueco declarado y medible. Todo
lo que se refiere a live sale como ``None`` con su motivo, nunca como cero.

**La honestidad incómoda que hay que decir por delante.** Con sólo paper e
ideal, el "gap de ejecución" que se mide es —por construcción— el slippage y el
spread que el propio simulador modeló. No es una medición independiente de nada:
es el modelo mirándose al espejo. Su valor real es servir de **línea base**: el
día que exista un carril live, la diferencia entre el gap modelado y el gap real
es lo que dirá si el simulador miente, y cuánto. Reportarlo hoy como "slippage
real" sería exactamente el tipo de cifra que engaña a quien la lee.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import ShadowBenchmarkSettings
from app.execution.models.trades import TradeRecord
from app.utils.time import utc_now

TradesProvider = Callable[[], Sequence[TradeRecord]]


@dataclass(frozen=True, kw_only=True, slots=True)
class TrackStats:
    """Resultado agregado de un carril de ejecución.

    Attributes:
        name: ``ideal`` / ``paper`` / ``live``.
        available: Si el carril tiene datos.
        reason: Por qué no, si no los tiene.
        trades: Operaciones del carril.
        net_pnl: PnL neto.
        gross_pnl: PnL bruto.
        avg_slippage_bps: Slippage medio registrado.
        avg_spread_bps: Spread medio registrado.
    """

    name: str
    available: bool = False
    reason: str = ""
    trades: int = 0
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    avg_slippage_bps: float | None = None
    avg_spread_bps: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "available": self.available,
            "reason": self.reason,
            "trades": self.trades,
            "net_pnl": round(self.net_pnl, 4),
            "gross_pnl": round(self.gross_pnl, 4),
            "avg_slippage_bps": _round(self.avg_slippage_bps),
            "avg_spread_bps": _round(self.avg_spread_bps),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ShadowReport:
    """Comparación entre carriles, con sus huecos declarados.

    Attributes:
        generated_at: Momento del cálculo.
        tracks: Cada carril con su estado.
        execution_gap_bps: Coste de ejecución medio frente al fill ideal.
        execution_gap_money: El mismo gap, en dinero de la cuenta.
        fill_difference_bps: Diferencia de fill entre live y paper. ``None``
            mientras no exista carril live — que es siempre, hoy.
        opportunity_cost_r: Lo que habrían rendido las señales no ejecutadas.
        recommendations: Qué hacer con lo medido.
        caveats: Qué **no** se puede concluir de este informe.
    """

    generated_at: datetime = field(default_factory=utc_now)
    tracks: dict[str, TrackStats] = field(default_factory=dict)
    execution_gap_bps: float | None = None
    execution_gap_money: float | None = None
    fill_difference_bps: float | None = None
    opportunity_cost_r: float | None = None
    recommendations: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "tracks": {name: track.to_dict() for name, track in sorted(self.tracks.items())},
            "execution_gap_bps": _round(self.execution_gap_bps),
            "execution_gap_money": _round(self.execution_gap_money),
            "fill_difference_bps": _round(self.fill_difference_bps),
            "opportunity_cost_r": _round(self.opportunity_cost_r),
            "recommendations": list(self.recommendations),
            # Los avisos viajan en la carga útil: sin ellos, el gap modelado se
            # lee como slippage real medido, que es justo lo que no es.
            "caveats": list(self.caveats),
            "live_enabled": False,
        }


class ShadowBenchmark:
    """Compare paper fills against the ideal fill, and leave live declared.

    Args:
        settings: Mínimos y umbrales de las recomendaciones.
        trades_provider: Fuente de operaciones de paper (Trade Journal).
        live_trades_provider: Fuente de operaciones **live**. Se acepta como
            parámetro para que el hueco sea explícito en la firma, pero hoy
            siempre es ``None`` y el motor **no puede** habilitarlo: no conoce
            ningún bróker ni ninguna ruta de ejecución.
        opportunity_cost_provider: Coste de oportunidad ya medido (Bloque 9).
    """

    def __init__(
        self,
        settings: ShadowBenchmarkSettings,
        trades_provider: TradesProvider,
        live_trades_provider: TradesProvider | None = None,
        opportunity_cost_provider: Callable[[], float | None] | None = None,
    ) -> None:
        self._settings = settings
        self._trades = trades_provider
        self._live = live_trades_provider
        self._opportunity = opportunity_cost_provider
        self._last: ShadowReport | None = None
        self._log = logging.getLogger("app.execution.benchmark")

    def analyze(self) -> ShadowReport:
        """Compare the tracks and produce recommendations.

        Returns:
            El informe, con el carril live declarado como ausente y los avisos
            que impiden leer el gap modelado como si fuera una medición.
        """
        paper_trades = list(self._trades())[-self._settings.max_trades :]
        tracks = {
            "paper": _track("paper", paper_trades),
            "ideal": _ideal_track(paper_trades),
            "live": self._live_track(),
        }
        gap_bps, gap_money = _execution_gap(paper_trades)
        report = ShadowReport(
            generated_at=utc_now(),
            tracks=tracks,
            execution_gap_bps=gap_bps,
            execution_gap_money=gap_money,
            # Sin carril live no hay diferencia de fill que medir. Cero diría
            # "paper y live coinciden", que es una afirmación sobre algo que no
            # se ha observado ni una sola vez.
            fill_difference_bps=None,
            opportunity_cost_r=self._opportunity() if self._opportunity is not None else None,
            recommendations=self._recommendations(paper_trades, gap_bps),
            caveats=_caveats(len(paper_trades), self._settings.min_sample),
        )
        self._last = report
        return report

    def _live_track(self) -> TrackStats:
        """The live track, which is always absent by project rule."""
        if self._live is None:
            return TrackStats(
                name="live",
                available=False,
                reason=(
                    "live trading deshabilitado por regla del proyecto: no hay "
                    "operaciones reales con las que comparar"
                ),
            )
        trades = list(self._live())
        if not trades:
            return TrackStats(name="live", available=False, reason="sin operaciones live")
        return _track("live", trades)

    def _recommendations(
        self, trades: Sequence[TradeRecord], gap_bps: float | None
    ) -> tuple[str, ...]:
        """Turn the measurements into things worth doing.

        Con muestra por debajo del mínimo **no se recomienda nada**: una
        recomendación es una llamada a la acción, y emitirla sobre diez
        operaciones es peor que callarse.
        """
        if len(trades) < self._settings.min_sample or gap_bps is None:
            return ()
        out: list[str] = []
        if gap_bps > self._settings.high_gap_bps:
            out.append(
                f"El coste de ejecución modelado ({gap_bps:.1f} bps) supera el umbral "
                f"({self._settings.high_gap_bps} bps): revisar el Execution Optimizer "
                "(Bloque 6) antes de subir tamaño"
            )
        avg_r = sum(t.r_multiple for t in trades) / len(trades)
        if avg_r > 0 and gap_bps > 0:
            # Cuánto del edge se lo come la ejecución, en la misma unidad.
            share = gap_bps / max(avg_r * 100.0, 1e-9)
            if share > self._settings.high_edge_share:
                out.append(
                    f"La ejecución se lleva el {share:.0%} de la R media: el edge es real "
                    "pero está mal capturado, y eso se arregla en ejecución, no en señal"
                )
        out.append(
            "El gap frente al fill ideal es la línea base: sólo se convierte en una "
            "medición del simulador cuando exista un carril live con el que contrastarlo"
        )
        return tuple(out)

    def last_report(self) -> ShadowReport | None:
        """Informe del último análisis."""
        return self._last

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            # Invariante del bloque, expuesto para que sea verificable desde
            # fuera y no sólo por lectura del código.
            "live_enabled": False,
            "last_report": None if self._last is None else self._last.to_dict(),
        }


# ---------------------------------------------------------------------------
# Carriles
# ---------------------------------------------------------------------------


def _track(name: str, trades: Sequence[TradeRecord]) -> TrackStats:
    """Aggregate one execution track."""
    if not trades:
        return TrackStats(name=name, available=False, reason="sin operaciones")
    return TrackStats(
        name=name,
        available=True,
        trades=len(trades),
        net_pnl=sum(t.pnl for t in trades),
        gross_pnl=sum(t.pnl_gross for t in trades),
        avg_slippage_bps=sum(t.slippage_bps for t in trades) / len(trades),
        avg_spread_bps=sum(t.spread_bps for t in trades) / len(trades),
    )


def _ideal_track(trades: Sequence[TradeRecord]) -> TrackStats:
    """The same trades as if execution had cost nothing.

    El fill ideal no es una simulación aparte: es el resultado real **más** los
    costes de ejecución que se le descontaron. Reconstruirlo con otro simulador
    introduciría un segundo modelo y con él una diferencia que no vendría de la
    ejecución sino de la discrepancia entre los dos modelos.
    """
    if not trades:
        return TrackStats(name="ideal", available=False, reason="sin operaciones")
    cost = sum(_execution_cost_money(t) for t in trades)
    return TrackStats(
        name="ideal",
        available=True,
        trades=len(trades),
        net_pnl=sum(t.pnl for t in trades) + cost,
        gross_pnl=sum(t.pnl_gross for t in trades),
        avg_slippage_bps=0.0,
        avg_spread_bps=0.0,
    )


def _execution_gap(trades: Sequence[TradeRecord]) -> tuple[float | None, float | None]:
    """Average execution cost against the ideal fill, in bps and in money."""
    if not trades:
        return None, None
    bps = sum(t.slippage_bps + t.spread_bps for t in trades) / len(trades)
    money = sum(_execution_cost_money(t) for t in trades)
    return bps, money


def _execution_cost_money(trade: TradeRecord) -> float:
    """Execution cost of one trade, over units (never over lots).

    Mismo criterio que ADR-099 y que el Bloque 9: en oro un lote son 100 onzas,
    y medir sobre `quantity` dejaría el coste 100 veces por debajo.
    """
    notional = trade.entry_price * trade.quantity * trade.contract_size
    return notional * (trade.slippage_bps + trade.spread_bps) / 10_000.0


def _caveats(sample: int, minimum: int) -> tuple[str, ...]:
    """What this report cannot be used to conclude."""
    out = [
        "sin carril live, el gap medido es el slippage y el spread que el propio "
        "simulador modeló: es una línea base, no una medición independiente",
        "live trading permanece deshabilitado; este informe no lo habilita ni lo prepara",
    ]
    if sample < minimum:
        out.append(f"muestra {sample} < mínimo {minimum}: no se emiten recomendaciones")
    return tuple(out)


def _round(value: float | None, digits: int = 4) -> float | None:
    """Round without turning ``None`` into a number."""
    return None if value is None else round(value, digits)
