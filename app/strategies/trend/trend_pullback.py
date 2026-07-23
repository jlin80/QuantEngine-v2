"""Trend Pullback: retroceso a la EMA en tendencia estructural confirmada.

Supuesto: en tendencia (HH/HL o LH/LL), el retroceso a la media dinámica con
vela de reanudación ofrece continuación con riesgo acotado. Limitación:
tarda en reconocer giros — el CHOCH/MSS del lado SMC cubre ese hueco.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import StructureAnalysis
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import body_ratio, clamp01, is_bearish, is_bullish


class TrendPullback(QuantStrategy):
    """Continuación tras retroceso a la EMA en tendencia."""

    name = "trend_pullback"
    version = "1.0"
    category: ClassVar[str] = "trend"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("trending",)
    default_parameters: ClassVar[dict[str, Any]] = {
        "ema_period": 20,
        "proximity_atr": 0.5,
        "stop_buffer_atr": 0.5,
        "risk_reward": 2.0,
        "confirmations": ["volume"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Tendencia estructural + toque de EMA + vela de reanudación."""
        structure = await ctx.features.get_object(
            "structure", ctx.symbol, timeframe=self.timeframe.value
        )
        ema = await ctx.features.get(
            "ema", ctx.symbol, timeframe=self.timeframe.value, period=self.iparam("ema_period")
        )
        atr = await self.atr_or_none(ctx)
        if not isinstance(structure, StructureAnalysis) or ema is None or atr is None or atr <= 0:
            return None
        last, prev = candles[-1], candles[-2]
        proximity = self.fparam("proximity_atr") * atr

        if structure.trend == "up":
            touched = min(last.low, prev.low) <= ema + proximity
            resumed = is_bullish(last) and last.close > ema
            if not (touched and resumed):
                return None
            direction = Direction.LONG
            reference = min(last.low, prev.low)
            target = (
                structure.last_swing_high.price if structure.last_swing_high is not None else None
            )
        elif structure.trend == "down":
            touched = max(last.high, prev.high) >= ema - proximity
            resumed = is_bearish(last) and last.close < ema
            if not (touched and resumed):
                return None
            direction = Direction.SHORT
            reference = max(last.high, prev.high)
            target = (
                structure.last_swing_low.price if structure.last_swing_low is not None else None
            )
        else:
            return None

        stop = protective_stop(direction, reference, atr, buffer_atr=self.fparam("stop_buffer_atr"))
        entry = entry_zone_around(last.close, atr)
        entry_mid = (entry.low + entry.high) / 2.0
        if target is None:
            target = target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward"))
        labels = [s.label for s in structure.swings[-4:] if s.label]

        return Assessment(
            direction=direction,
            quality=clamp01(1.0 - abs(last.close - ema) / (atr * 2.0)),
            strength=body_ratio(last),
            context_fit=1.0,
            probability=0.55,
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Tendencia {structure.trend} confirmada ({'/'.join(labels) or 'estructura'}).",
                f"Retroceso a la EMA{self.iparam('ema_period')} ({ema:.4f}) "
                "con vela de reanudación.",
            ],
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            metadata={"ema": round(ema, 6), "trend": structure.trend},
        )
