"""VWAP Breakout: continuación al cruzar la primera banda σ con pendiente.

Supuesto: un cierre fuera de la banda 1σ con VWAP inclinado a favor y
volumen indica desequilibrio sostenido. Limitación: usa las bandas actuales
para leer el cruce (aproximación de una barra, documentada).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import VWAPBands
from app.engine.events import VWAPCalculated
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import body_ratio, clamp01


class VWAPBreakout(QuantStrategy):
    """Ruptura de la banda 1σ del VWAP de sesión, a favor de la pendiente."""

    name = "vwap_breakout"
    version = "1.0"
    category: ClassVar[str] = "breakout"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("trending", "breakout", "expansion")
    default_parameters: ClassVar[dict[str, Any]] = {
        "anchor": "day",
        "min_slope_pct": 0.002,
        "stop_buffer_atr": 0.25,
        "risk_reward": 2.0,
        "confirmations": ["volume", "delta"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Cierre cruzando la banda 1σ con pendiente del VWAP a favor."""
        anchor = str(self.parameters["anchor"])
        bands = await ctx.features.get_object(
            "vwap_bands", ctx.symbol, timeframe=self.timeframe.value, anchor=anchor
        )
        atr = await self.atr_or_none(ctx)
        if not isinstance(bands, VWAPBands) or bands.std <= 0 or bands.bars < 10:
            return None
        if atr is None or atr <= 0:
            return None

        last, prev = candles[-1], candles[-2]
        min_slope = self.fparam("min_slope_pct")
        if prev.close <= bands.upper_1 < last.close and bands.slope_pct_per_bar >= min_slope:
            direction = Direction.LONG
            band = bands.upper_1
        elif prev.close >= bands.lower_1 > last.close and bands.slope_pct_per_bar <= -min_slope:
            direction = Direction.SHORT
            band = bands.lower_1
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
        stop = protective_stop(
            direction, bands.value, atr, buffer_atr=self.fparam("stop_buffer_atr")
        )
        entry = entry_zone_around(last.close, atr)
        entry_mid = (entry.low + entry.high) / 2.0
        beyond_sigma = abs(last.close - band) / bands.std

        return Assessment(
            direction=direction,
            quality=clamp01(0.4 + beyond_sigma),
            strength=body_ratio(last),
            context_fit=clamp01(0.5 + abs(bands.slope_pct_per_bar) / (min_slope * 4)),
            probability=clamp01(0.4 + beyond_sigma * 0.2),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Cierre {'sobre' if direction is Direction.LONG else 'bajo'} la banda 1σ "
                f"del VWAP {anchor} ({band:.4f}).",
                f"Pendiente del VWAP a favor ({bands.slope_pct_per_bar:+.3f}%/bar).",
            ],
            entry=entry,
            stop_loss=stop,
            take_profit=target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward")),
            metadata={"vwap": bands.value, "band": band},
        )
