"""Volume Profile: rechazo en los extremos del value area, objetivo el POC.

Supuesto: en mercados en rango, el precio fuera del value area con rechazo
regresa al POC (donde está el volumen). Limitación: en tendencia el value
area migra — el encaje de régimen degrada la señal fuera de rango.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import VolumeProfile
from app.engine.events import VolumeProfileUpdated
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop
from app.strategies.utils import clamp01, lower_wick_ratio, upper_wick_ratio


class VolumeProfileStrategy(QuantStrategy):
    """Reversión desde VAL/VAH hacia el POC con mecha de rechazo."""

    name = "volume_profile"
    version = "1.0"
    category: ClassVar[str] = "volume"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("ranging", "compression")
    default_parameters: ClassVar[dict[str, Any]] = {
        "min_rejection_wick": 0.35,
        "stop_buffer_atr": 0.5,
        "confirmations": ["volume", "delta"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Re-entrada al value area con mecha de rechazo en VAL/VAH."""
        profile = await ctx.features.get_object(
            "volume_profile", ctx.symbol, timeframe=self.timeframe.value
        )
        atr = await self.atr_or_none(ctx)
        if not isinstance(profile, VolumeProfile) or atr is None or atr <= 0:
            return None
        last = candles[-1]
        min_wick = self.fparam("min_rejection_wick")

        if last.low <= profile.val and last.close > profile.val:
            wick = lower_wick_ratio(last)
            if wick < min_wick:
                return None
            direction = Direction.LONG
            reference = last.low
            edge = profile.val
        elif last.high >= profile.vah and last.close < profile.vah:
            wick = upper_wick_ratio(last)
            if wick < min_wick:
                return None
            direction = Direction.SHORT
            reference = last.high
            edge = profile.vah
        else:
            return None

        ctx.detections.append(
            VolumeProfileUpdated(
                source=self.name,
                symbol=ctx.symbol,
                poc=profile.poc,
                vah=profile.vah,
                val=profile.val,
            )
        )
        stop = protective_stop(direction, reference, atr, buffer_atr=self.fparam("stop_buffer_atr"))
        distance_to_poc = abs(profile.poc - last.close)

        return Assessment(
            direction=direction,
            quality=clamp01(wick * 1.5),
            strength=clamp01(distance_to_poc / (atr * 3.0)),
            context_fit=0.9,
            probability=clamp01(0.4 + wick * 0.3),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Rechazo en {'VAL' if direction is Direction.LONG else 'VAH'} "
                f"({edge:.4f}) con mecha del {wick:.0%}.",
                f"Objetivo: POC en {profile.poc:.4f} "
                f"(value area {profile.val:.4f}–{profile.vah:.4f}).",
            ],
            entry=entry_zone_around(last.close, atr),
            stop_loss=stop,
            take_profit=profile.poc,
            metadata={"poc": profile.poc, "vah": profile.vah, "val": profile.val},
        )
