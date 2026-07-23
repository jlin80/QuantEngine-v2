"""Mean Reversion clásica: fade de z-scores extremos fuera de tendencia.

Supuesto: fuera de tendencia, un cierre a ≥ Nσ de su media rueda revierte
hacia ella. Limitación: prohibida en régimen trending (el encaje de régimen
la anula); el stop va más allá del extremo por si la cola sigue.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction, Regime
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop
from app.strategies.utils import body_ratio, clamp01, mean, zscore


class MeanReversion(QuantStrategy):
    """Reversión a la media por z-score extremo."""

    name = "mean_reversion"
    version = "1.0"
    category: ClassVar[str] = "mean_reversion"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("ranging", "compression")
    preferred_volatility: ClassVar[tuple[str, ...]] = ("normal",)
    default_parameters: ClassVar[dict[str, Any]] = {
        "z_window": 40,
        "z_entry": 2.0,
        "stop_buffer_atr": 0.75,
        "confirmations": ["volatility", "volume"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Z-score extremo del cierre, fuera de régimen trending."""
        regime = ctx.context.regime.primary if ctx.context.regime else Regime.UNKNOWN
        if regime in (Regime.TRENDING, Regime.BREAKOUT):
            return None
        atr = await self.atr_or_none(ctx)
        if atr is None or atr <= 0:
            return None
        window = candles[-self.iparam("z_window") :]
        closes = [c.close for c in window]
        z = zscore(closes)
        avg = mean(closes)
        if z is None or avg is None or abs(z) < self.fparam("z_entry"):
            return None

        last = candles[-1]
        if z <= -self.fparam("z_entry"):
            direction = Direction.LONG
            reference = min(c.low for c in candles[-3:])
        else:
            direction = Direction.SHORT
            reference = max(c.high for c in candles[-3:])
        stop = protective_stop(direction, reference, atr, buffer_atr=self.fparam("stop_buffer_atr"))

        return Assessment(
            direction=direction,
            quality=clamp01(abs(z) / 3.0),
            strength=body_ratio(last),
            context_fit=0.9,
            probability=clamp01(0.3 + abs(z) * 0.1),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Cierre a {z:+.2f}σ de la media de {self.iparam('z_window')} velas "
                f"({avg:.4f}).",
                f"Régimen {regime.value}: condiciones de reversión.",
                "Objetivo: retorno a la media.",
            ],
            entry=entry_zone_around(last.close, atr),
            stop_loss=stop,
            take_profit=avg,
            metadata={"zscore": round(z, 3), "mean": round(avg, 6)},
        )
