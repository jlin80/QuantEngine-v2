"""VWAP Mean Reversion: fade de extensiones extremas contra el VWAP de sesión.

Supuesto: en mercados laterales, el precio estirado ≥ Nσ del VWAP con vela de
reversión tiende a regresar al VWAP. Limitación: en tendencia fuerte el
estiramiento persiste — el encaje de régimen y el consenso lo penalizan.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import VWAPBands
from app.engine.events import VWAPCalculated
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop
from app.strategies.utils import body_ratio, clamp01, is_bearish, is_bullish


class VWAPMeanReversion(QuantStrategy):
    """Reversión al VWAP de sesión desde una banda extrema."""

    name = "vwap_mean_reversion"
    version = "1.0"
    category: ClassVar[str] = "mean_reversion"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("ranging", "compression")
    preferred_volatility: ClassVar[tuple[str, ...]] = ("normal",)
    default_parameters: ClassVar[dict[str, Any]] = {
        "anchor": "day",
        "entry_deviation": 2.0,
        "stop_extra_atr": 0.75,
        "min_reversal_body": 0.25,
        "confirmations": ["delta", "volume"],
        "min_signal_score": 50.0,
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Busca extensión ≥ Nσ del VWAP con vela de reversión."""
        anchor = str(self.parameters["anchor"])
        bands = await ctx.features.get_object(
            "vwap_bands", ctx.symbol, timeframe=self.timeframe.value, anchor=anchor
        )
        atr = await self.atr_or_none(ctx)
        if not isinstance(bands, VWAPBands) or bands.std <= 0 or bands.bars < 10:
            return None
        if atr is None or atr <= 0:
            return None

        last = candles[-1]
        deviation = bands.deviation(last.close)
        entry_dev = self.fparam("entry_deviation")
        body = body_ratio(last)

        if (
            deviation <= -entry_dev
            and is_bullish(last)
            and body >= self.fparam("min_reversal_body")
        ):
            direction = Direction.LONG
        elif (
            deviation >= entry_dev and is_bearish(last) and body >= self.fparam("min_reversal_body")
        ):
            direction = Direction.SHORT
        else:
            return None

        ctx.detections.append(
            VWAPCalculated(
                source=self.name,
                symbol=ctx.symbol,
                kind=anchor,
                value=bands.value,
                slope_pct_per_bar=bands.slope_pct_per_bar,
            )
        )
        extremes = candles[-3:]
        if direction is Direction.LONG:
            reference = min(c.low for c in extremes)
        else:
            reference = max(c.high for c in extremes)
        stop = protective_stop(direction, reference, atr, buffer_atr=self.fparam("stop_extra_atr"))

        warnings: list[str] = []
        slope_against = (direction is Direction.LONG and bands.slope_pct_per_bar < 0) or (
            direction is Direction.SHORT and bands.slope_pct_per_bar > 0
        )
        if slope_against:
            warnings.append(f"Pendiente del VWAP en contra ({bands.slope_pct_per_bar:+.3f}%/bar).")

        return Assessment(
            direction=direction,
            quality=clamp01(abs(deviation) / (entry_dev * 1.5)),
            strength=body,
            context_fit=1.0 if not slope_against else 0.6,
            probability=clamp01(0.35 + 0.12 * abs(deviation)),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Precio a {deviation:+.2f}σ del VWAP {anchor} ({bands.value:.4f}).",
                f"Vela de reversión con cuerpo {body:.0%}.",
                "Objetivo: retorno al VWAP de sesión.",
            ],
            warnings=warnings,
            entry=entry_zone_around(last.close, atr),
            stop_loss=stop,
            take_profit=bands.value,
            metadata={"vwap": bands.value, "deviation_sigma": round(deviation, 3)},
        )
