"""Anchored VWAP: pullback al VWAP anclado en el último swing estructural.

Supuesto: en tendencia, el VWAP anclado al swing de origen actúa como valor
dinámico donde el interés institucional reaparece. Limitación: exige swings
confirmados (fractal), llega tarde en tendencias jóvenes.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import StructureAnalysis, anchored_vwap
from app.engine.events import VWAPCalculated
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import body_ratio, clamp01, is_bearish, is_bullish


class AnchoredVWAP(QuantStrategy):
    """Rebote en el VWAP anclado al último swing low/high de la tendencia."""

    name = "anchored_vwap"
    version = "1.0"
    category: ClassVar[str] = "trend"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("trending",)
    default_parameters: ClassVar[dict[str, Any]] = {
        "proximity_atr": 0.5,
        "stop_buffer_atr": 0.75,
        "risk_reward": 2.0,
        "confirmations": ["volume"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Pullback que toca el AVWAP con vela de reanudación."""
        structure = await ctx.features.get_object(
            "structure", ctx.symbol, timeframe=self.timeframe.value
        )
        atr = await self.atr_or_none(ctx)
        if not isinstance(structure, StructureAnalysis) or atr is None or atr <= 0:
            return None

        last = candles[-1]
        proximity = self.fparam("proximity_atr") * atr

        if structure.trend == "up" and structure.last_swing_low is not None:
            avwap = anchored_vwap(candles, structure.last_swing_low.index)
            if avwap is None:
                return None
            touched = last.low <= avwap + proximity
            resumed = is_bullish(last) and last.close > avwap
            if not (touched and resumed):
                return None
            direction = Direction.LONG
            target = (
                structure.last_swing_high.price if structure.last_swing_high is not None else None
            )
        elif structure.trend == "down" and structure.last_swing_high is not None:
            avwap = anchored_vwap(candles, structure.last_swing_high.index)
            if avwap is None:
                return None
            touched = last.high >= avwap - proximity
            resumed = is_bearish(last) and last.close < avwap
            if not (touched and resumed):
                return None
            direction = Direction.SHORT
            target = (
                structure.last_swing_low.price if structure.last_swing_low is not None else None
            )
        else:
            return None

        ctx.detections.append(
            VWAPCalculated(source=self.name, symbol=ctx.symbol, kind="anchored", value=avwap)
        )
        stop = protective_stop(direction, avwap, atr, buffer_atr=self.fparam("stop_buffer_atr"))
        entry = entry_zone_around(last.close, atr)
        entry_mid = (entry.low + entry.high) / 2.0
        if target is None:
            target = target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward"))

        return Assessment(
            direction=direction,
            quality=clamp01(1.0 - abs(last.close - avwap) / (atr * 2.0)),
            strength=body_ratio(last),
            context_fit=1.0,
            probability=0.55,
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Tendencia {structure.trend} con pullback al AVWAP ({avwap:.4f}) "
                "anclado al último swing.",
                "Vela de reanudación a favor de la tendencia.",
            ],
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            metadata={"anchored_vwap": avwap, "trend": structure.trend},
        )
