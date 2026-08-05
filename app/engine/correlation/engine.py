"""Correlation Intelligence (Bloque 5) — cómo se mueven los activos entre sí.

Responde cinco preguntas que el motor no se hacía: quién lidera a quién, qué
pares vuelven a su relación, cuánto se parecen ahora frente a de costumbre, qué
símbolo manda en el conjunto, y si eso cambia por sesión.

**Para qué sirve realmente.** El filtro de correlación existente veta por grupos
declarados a mano en configuración. Un grupo escrito hace meses envejece: dos
símbolos pueden dejar de moverse juntos, o empezar a hacerlo, sin que nadie
actualice el fichero. Este motor mide la correlación real y el filtro puede
consultarla — sin sustituir a los grupos manuales, que siguen valiendo como
regla dura.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import QuantCorrelationSettings
from app.core.lifecycle import Service
from app.engine.correlation.stats import (
    correlation,
    ewma_correlation,
    hedge_ratio,
    lead_lag,
    residual_half_life,
    returns,
)
from app.market.models import Candle, Timeframe
from app.market.services import MarketDataService
from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class PairRelation:
    """Relación medida entre dos símbolos.

    Attributes:
        left: Primer símbolo (el candidato a líder).
        right: Segundo símbolo.
        rolling: Correlación de rendimientos en la ventana completa.
        dynamic: Correlación con pesos exponenciales (la de *ahora*).
        lead_bars: Barras que ``left`` va por delante (negativo = va detrás,
            0 = simultáneo, es decir, sin liderazgo que reportar).
        lead_correlation: Correlación en ese desplazamiento.
        hedge_ratio: Pendiente de mínimos cuadrados entre precios.
        residual_half_life: Barras que tarda el residuo en volver a su media.
            ``None`` = **no revierte**, que es la respuesta útil para descartar
            un par, no un fallo de cálculo.
        sample: Observaciones comunes.
    """

    left: str
    right: str
    rolling: float | None = None
    dynamic: float | None = None
    lead_bars: int | None = None
    lead_correlation: float | None = None
    hedge_ratio: float | None = None
    residual_half_life: float | None = None
    sample: int = 0

    @property
    def divergence(self) -> float | None:
        """Cuánto se ha despegado la correlación de ahora de la de costumbre.

        Es la lectura que importa operativamente: no el nivel de correlación,
        sino su **cambio**. Un par que siempre va a 0.9 y hoy va a 0.4 está
        diciendo algo; uno que siempre va a 0.4 no.
        """
        if self.rolling is None or self.dynamic is None:
            return None
        return self.dynamic - self.rolling

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "left": self.left,
            "right": self.right,
            "rolling": _round(self.rolling),
            "dynamic": _round(self.dynamic),
            "divergence": _round(self.divergence),
            "lead_bars": self.lead_bars,
            "lead_correlation": _round(self.lead_correlation),
            "hedge_ratio": _round(self.hedge_ratio),
            "residual_half_life": _round(self.residual_half_life, 2),
            "sample": self.sample,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class CorrelationReport:
    """Fotografía de las relaciones entre todos los símbolos seguidos."""

    generated_at: datetime = field(default_factory=utc_now)
    pairs: tuple[PairRelation, ...] = ()
    leadership: dict[str, float] = field(default_factory=dict)
    sessions: dict[str, dict[str, float]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "pairs": [pair.to_dict() for pair in self.pairs],
            "leadership": {k: round(v, 4) for k, v in self.leadership.items()},
            "sessions": self.sessions,
            # Qué pares no se pudieron medir y por qué. Un informe de
            # correlación que omite en silencio la mitad de los pares invita a
            # concluir que el resto no está correlacionado.
            "skipped": self.skipped,
        }


class CorrelationEngine(Service):
    """Measure how the tracked symbols move relative to each other.

    Args:
        settings: Ventanas, mínimos y umbrales.
        market: API de datos (velas por símbolo).
        symbols: Símbolos a relacionar.
        timeframe: Marco temporal de las velas.
    """

    def __init__(
        self,
        settings: QuantCorrelationSettings,
        market: MarketDataService,
        symbols: list[str],
        *,
        timeframe: Timeframe = Timeframe.M5,
    ) -> None:
        super().__init__("correlation")
        self._settings = settings
        self._market = market
        self._symbols = symbols
        self._timeframe = timeframe
        self._last: CorrelationReport | None = None
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger("app.engine.correlation")

    def analyze(self) -> CorrelationReport:
        """Measure every pair, then rank leadership from the pair results.

        Returns:
            El informe. Los pares no medibles se declaran en ``skipped`` con su
            motivo en vez de omitirse.
        """
        series = self._series()
        pairs: list[PairRelation] = []
        skipped: dict[str, str] = {}
        for i, left in enumerate(self._symbols):
            for right in self._symbols[i + 1 :]:
                key = f"{left}~{right}"
                relation = self._relation(left, right, series)
                if relation is None:
                    skipped[key] = "muestra insuficiente o serie constante"
                    continue
                pairs.append(relation)
        report = CorrelationReport(
            generated_at=utc_now(),
            pairs=tuple(pairs),
            leadership=_leadership(pairs, self._settings.min_lead_correlation),
            sessions=self._session_correlations(),
            skipped=skipped,
        )
        self._last = report
        return report

    def _series(self) -> dict[str, list[Candle]]:
        """Candle series per tracked symbol."""
        return {
            symbol: list(self._market.get_candles(symbol, self._timeframe, self._settings.window))
            for symbol in self._symbols
        }

    def _relation(
        self, left: str, right: str, series: dict[str, list[Candle]]
    ) -> PairRelation | None:
        """Measure one pair, or ``None`` if it is not measurable."""
        left_candles = series.get(left, [])
        right_candles = series.get(right, [])
        size = min(len(left_candles), len(right_candles))
        if size < self._settings.min_sample:
            return None
        # Se alinean por el FINAL: las series pueden tener distinta longitud
        # (un símbolo con menos histórico), y alinear por el principio
        # compararía la semana pasada de uno con la de hace un mes del otro.
        left_prices = [c.close for c in left_candles[-size:]]
        right_prices = [c.close for c in right_candles[-size:]]
        left_returns = returns(left_prices)
        right_returns = returns(right_prices)
        rolling = correlation(left_returns, right_returns)
        if rolling is None:
            return None
        beta = hedge_ratio(left_prices, right_prices)
        half_life = None
        if beta is not None:
            residual = [left_prices[i] - beta * right_prices[i] for i in range(size)]
            half_life = residual_half_life(residual)
        best = lead_lag(left_returns, right_returns, self._settings.max_lag)
        return PairRelation(
            left=left,
            right=right,
            rolling=rolling,
            dynamic=ewma_correlation(left_returns, right_returns, self._settings.ewma_halflife),
            lead_bars=None if best is None else best[0],
            lead_correlation=None if best is None else best[1],
            hedge_ratio=beta,
            residual_half_life=half_life,
            sample=size,
        )

    def _session_correlations(self) -> dict[str, dict[str, float]]:
        """Correlation per session bucket, for every measurable pair.

        La correlación por sesión no es un adorno: dos símbolos pueden ir de la
        mano en la sesión americana y separarse en la asiática, y un filtro que
        use la media de las dos se equivoca en ambas.
        """
        buckets: dict[str, dict[str, list[float]]] = {}
        for symbol in self._symbols:
            candles = list(self._market.get_candles(symbol, self._timeframe, self._settings.window))
            for candle in candles:
                session = _session_of(candle.start)
                buckets.setdefault(session, {}).setdefault(symbol, []).append(candle.close)
        out: dict[str, dict[str, float]] = {}
        for session, prices in sorted(buckets.items()):
            values: dict[str, float] = {}
            for i, left in enumerate(self._symbols):
                for right in self._symbols[i + 1 :]:
                    left_prices = prices.get(left, [])
                    right_prices = prices.get(right, [])
                    size = min(len(left_prices), len(right_prices))
                    if size < self._settings.min_sample:
                        continue
                    value = correlation(returns(left_prices[-size:]), returns(right_prices[-size:]))
                    if value is not None:
                        values[f"{left}~{right}"] = round(value, 4)
            if values:
                out[session] = values
        return out

    def correlated_with(self, symbol: str) -> set[str]:
        """Symbols measurably correlated with the given one right now.

        Es el hook del filtro de correlación: los grupos declarados a mano
        siguen valiendo como regla dura, y esto añade lo que el mercado está
        haciendo de verdad. Sin informe todavía devuelve vacío — nunca inventa
        una correlación que no se ha medido.

        Args:
            symbol: Símbolo consultado.

        Returns:
            Los símbolos correlacionados por encima del umbral configurado.
        """
        if self._last is None:
            return set()
        found: set[str] = set()
        for pair in self._last.pairs:
            if pair.dynamic is None or abs(pair.dynamic) < self._settings.min_correlation:
                continue
            if pair.left == symbol:
                found.add(pair.right)
            elif pair.right == symbol:
                found.add(pair.left)
        return found

    def last_report(self) -> CorrelationReport | None:
        """Informe del último análisis."""
        return self._last

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "symbols": list(self._symbols),
            "last_report": None if self._last is None else self._last.to_dict(),
        }

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Re-measure on its own cadence, forever."""
        while True:
            await asyncio.sleep(self._settings.cycle_interval_seconds)
            try:
                self.analyze()
            except Exception:  # medir correlacion jamas puede tumbar al motor
                self._log.exception("Correlation cycle failed")

    async def _on_start(self) -> None:
        if not self._settings.enabled:
            return
        # Una medicion inmediata al arrancar: si no, el filtro de correlacion
        # pasaria el primer intervalo entero sin la evidencia que este motor
        # existe para darle, y nadie sabria por que.
        try:
            self.analyze()
        except Exception:
            self._log.exception("Initial correlation analysis failed")
        self._task = asyncio.create_task(self._loop(), name="correlation")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def _leadership(pairs: list[PairRelation], min_correlation: float) -> dict[str, float]:
    """Rank symbols by how often they lead the pairs they appear in.

    Sólo cuentan los liderazgos con correlación suficiente: un lag "ganador"
    con correlación de 0.05 es ruido con signo, y sumarlo al ranking convierte
    el liderazgo en una lotería.
    """
    score: dict[str, float] = {}
    for pair in pairs:
        if pair.lead_bars is None or pair.lead_correlation is None:
            continue
        if abs(pair.lead_correlation) < min_correlation or pair.lead_bars == 0:
            continue
        leader = pair.left if pair.lead_bars > 0 else pair.right
        score[leader] = score.get(leader, 0.0) + abs(pair.lead_correlation)
    return dict(sorted(score.items(), key=lambda item: -item[1]))


def _session_of(moment: datetime) -> str:
    """Session bucket of a UTC timestamp (mismo criterio que el contexto)."""
    hour = moment.hour
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 13:
        return "europe"
    if 13 <= hour < 21:
        return "america"
    return "late"


def _round(value: float | None, digits: int = 4) -> float | None:
    """Round without turning ``None`` into a number."""
    return None if value is None else round(value, digits)
