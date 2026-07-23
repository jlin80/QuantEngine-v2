"""Momentum Continuation: impulso + retroceso superficial + reanudación.

Supuesto: un impulso con cuerpos dominantes seguido de un retroceso ≤ N% que
se reanuda continúa el movimiento. Limitación: en agotamiento el patrón
falla — el momentum score y las advertencias de order flow lo matizan.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import detect_impulse
from app.engine.events import MomentumDetected
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop
from app.strategies.utils import clamp01, is_bearish, is_bullish


class MomentumContinuation(QuantStrategy):
    """Continuación tras impulso con pullback superficial."""

    name = "momentum_continuation"
    version = "1.0"
    category: ClassVar[str] = "momentum"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("trending", "expansion", "breakout")
    default_parameters: ClassVar[dict[str, Any]] = {
        "impulse_max_bars": 3,
        "impulse_min_atr": 1.5,
        "max_retrace": 0.5,
        "pullback_bars": 4,
        "stop_buffer_atr": 0.5,
        "target_extension": 0.5,
        "confirmations": ["delta", "volume"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Impulso reciente, retroceso contenido y vela de reanudación."""
        atr = await self.atr_or_none(ctx)
        momentum = await ctx.features.get(
            "momentum_score", ctx.symbol, timeframe=self.timeframe.value
        )
        if atr is None or atr <= 0:
            return None
        pullback_bars = self.iparam("pullback_bars")
        if len(candles) <= pullback_bars + 5:
            return None

        base = candles[: len(candles) - pullback_bars]
        impulse = detect_impulse(
            base,
            max_bars=self.iparam("impulse_max_bars"),
            min_atr_multiple=self.fparam("impulse_min_atr"),
        )
        if impulse is None:
            return None
        window = base[impulse.start_index : impulse.end_index + 1]
        pullback = candles[len(base) :]
        last = candles[-1]
        impulse_range = impulse.magnitude * atr

        if impulse.direction == "up":
            extreme = max(c.high for c in window)
            pull_low = min(c.low for c in pullback)
            retrace = (extreme - pull_low) / impulse_range if impulse_range > 0 else 1.0
            resumed = (
                is_bullish(last) and last.close > pullback[-2].high
                if len(pullback) >= 2
                else is_bullish(last)
            )
            direction = Direction.LONG
            reference = pull_low
            target = extreme + impulse_range * self.fparam("target_extension")
            aligned = momentum is not None and momentum > 0
        else:
            extreme = min(c.low for c in window)
            pull_high = max(c.high for c in pullback)
            retrace = (pull_high - extreme) / impulse_range if impulse_range > 0 else 1.0
            resumed = (
                is_bearish(last) and last.close < pullback[-2].low
                if len(pullback) >= 2
                else is_bearish(last)
            )
            direction = Direction.SHORT
            reference = pull_high
            target = extreme - impulse_range * self.fparam("target_extension")
            aligned = momentum is not None and momentum < 0

        if retrace > self.fparam("max_retrace") or not resumed:
            return None

        ctx.detections.append(
            MomentumDetected(
                source=self.name,
                symbol=ctx.symbol,
                direction=impulse.direction,
                score=momentum if momentum is not None else 0.0,
            )
        )
        stop = protective_stop(direction, reference, atr, buffer_atr=self.fparam("stop_buffer_atr"))

        return Assessment(
            direction=direction,
            quality=clamp01(impulse.body_ratio),
            strength=clamp01(impulse.magnitude / 3.0),
            context_fit=0.9 if aligned else 0.5,
            probability=clamp01(0.55 - retrace * 0.2 + (0.1 if aligned else 0.0)),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Impulso {impulse.direction} de {impulse.magnitude:.1f} ATR "
                f"(cuerpos {impulse.body_ratio:.0%}).",
                f"Retroceso contenido ({retrace:.0%} ≤ "
                f"{self.fparam('max_retrace'):.0%}) y vela de reanudación.",
            ],
            warnings=[] if aligned else ["Momentum score no alineado con el impulso."],
            entry=entry_zone_around(last.close, atr),
            stop_loss=stop,
            take_profit=target,
            metadata={"impulse_atr": round(impulse.magnitude, 2), "retrace": round(retrace, 3)},
        )
