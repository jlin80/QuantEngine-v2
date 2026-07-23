"""Opening Range Breakout: primera ruptura del rango de apertura de sesión.

Supuesto: el rango de los primeros N minutos define la subasta inicial; la
primera ruptura con volumen marca la dirección del día. Limitación: en cripto
la "apertura" es el día UTC (configurable); solo opera la PRIMERA ruptura.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any, ClassVar

from app.analytics.indicators import session_anchor
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around
from app.strategies.utils import body_ratio, clamp01


class OpeningRangeBreakout(QuantStrategy):
    """Primera ruptura del rango de apertura con volumen."""

    name = "opening_range_breakout"
    version = "1.0"
    category: ClassVar[str] = "breakout"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("breakout", "expansion", "trending")
    default_parameters: ClassVar[dict[str, Any]] = {
        "range_minutes": 30.0,
        "min_volume_ratio": 1.2,
        "target_extension": 1.0,
        "confirmations": ["volume", "delta"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Cierre fuera del opening range, solo la primera vez del día."""
        anchor = session_anchor("day", ctx.fired_at)
        range_end = anchor + timedelta(minutes=self.fparam("range_minutes"))
        if ctx.fired_at <= range_end:
            return None
        opening = [c for c in candles if anchor <= c.start < range_end]
        after = [c for c in candles if c.start >= range_end]
        if len(opening) < 3 or not after:
            return None
        or_high = max(c.high for c in opening)
        or_low = min(c.low for c in opening)
        or_height = or_high - or_low
        if or_height <= 0:
            return None

        last = after[-1]
        prior = after[:-1]
        # Solo la primera ruptura del día: si ya hubo cierre fuera, no operar.
        if any(c.close > or_high or c.close < or_low for c in prior):
            return None
        if last.close > or_high:
            direction = Direction.LONG
            level = or_high
            target = last.close + or_height * self.fparam("target_extension")
        elif last.close < or_low:
            direction = Direction.SHORT
            level = or_low
            target = last.close - or_height * self.fparam("target_extension")
        else:
            return None

        volume_ratio = await ctx.features.get(
            "volume_ratio", ctx.symbol, timeframe=self.timeframe.value
        )
        ratio = volume_ratio if volume_ratio is not None else 0.0
        atr = await self.atr_or_none(ctx)
        if atr is None or atr <= 0:
            return None
        stop = (or_high + or_low) / 2.0  # mitad del opening range

        return Assessment(
            direction=direction,
            quality=clamp01(ratio / (self.fparam("min_volume_ratio") * 1.5)),
            strength=body_ratio(last),
            context_fit=0.85,
            probability=clamp01(0.45 + min(ratio - 1.0, 1.0) * 0.15),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Primera ruptura del opening range [{or_low:.4f}, {or_high:.4f}] "
                f"({self.fparam('range_minutes'):.0f} min).",
                f"Cierre {'sobre' if direction is Direction.LONG else 'bajo'} {level:.4f} "
                f"con volumen {ratio:.2f}x.",
            ],
            warnings=(
                [f"Volumen {ratio:.2f}x bajo el mínimo {self.fparam('min_volume_ratio'):.1f}x."]
                if ratio < self.fparam("min_volume_ratio")
                else []
            ),
            entry=entry_zone_around(last.close, atr),
            stop_loss=stop,
            take_profit=target,
            metadata={"or_high": or_high, "or_low": or_low, "volume_ratio": round(ratio, 2)},
        )
