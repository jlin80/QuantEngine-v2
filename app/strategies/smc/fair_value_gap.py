"""Fair Value Gap: entrada en la mitigación de un imbalance abierto.

Supuesto: los gaps de 3 velas dejan órdenes sin cruzar; el primer retorno al
gap (a favor de la ruptura vigente) suele reaccionar en su consecuente.
Limitación: gaps casi rellenos pierden interés (se filtra por fracción
abierta mínima).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import SMCAnalysis
from app.engine.events import FVGDetected
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction, EntryZone
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import protective_stop, target_from_rr
from app.strategies.utils import clamp01


class FairValueGapStrategy(QuantStrategy):
    """Mitigación de un FVG abierto alineado con la ruptura vigente."""

    name = "fair_value_gap"
    version = "1.0"
    category: ClassVar[str] = "smc"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("trending", "breakout")
    default_parameters: ClassVar[dict[str, Any]] = {
        "min_open_fraction": 0.5,
        "stop_buffer_atr": 0.5,
        "risk_reward": 2.0,
        "confirmations": ["delta"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Precio tocando un FVG abierto alineado con la última ruptura."""
        smc = await ctx.features.get_object("smc", ctx.symbol, timeframe=self.timeframe.value)
        if not isinstance(smc, SMCAnalysis) or not smc.breaks:
            return None
        wanted = "bullish" if smc.breaks[-1].direction == "up" else "bearish"
        min_open = self.fparam("min_open_fraction")
        gaps = [g for g in smc.fvgs if g.direction == wanted and g.open_fraction >= min_open]
        if not gaps:
            return None
        gap = gaps[-1]
        last = candles[-1]

        if gap.direction == "bullish":
            touching = last.low <= gap.top and last.close > gap.bottom
            direction = Direction.LONG
            reference = gap.bottom
        else:
            touching = last.high >= gap.bottom and last.close < gap.top
            direction = Direction.SHORT
            reference = gap.top
        if not touching:
            return None

        ctx.detections.append(
            FVGDetected(
                source=self.name,
                symbol=ctx.symbol,
                direction=gap.direction,
                top=gap.top,
                bottom=gap.bottom,
            )
        )
        stop = protective_stop(
            direction, reference, smc.atr, buffer_atr=self.fparam("stop_buffer_atr")
        )
        entry_mid = gap.mid
        highs = [s.price for s in smc.swings if s.kind == "high"]
        lows = [s.price for s in smc.swings if s.kind == "low"]
        if direction is Direction.LONG:
            target = (
                max(highs)
                if highs
                else target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward"))
            )
        else:
            target = (
                min(lows)
                if lows
                else target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward"))
            )
        gap_size_atr = (gap.top - gap.bottom) / smc.atr if smc.atr > 0 else 0.0

        return Assessment(
            direction=direction,
            quality=gap.open_fraction,
            strength=clamp01(gap_size_atr / 1.5),
            context_fit=0.85,
            probability=clamp01(0.4 + 0.2 * gap.open_fraction),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"FVG {gap.direction} abierto ({gap.open_fraction:.0%}) en "
                f"[{gap.bottom:.4f}, {gap.top:.4f}].",
                f"Alineado con la última ruptura ({smc.breaks[-1].kind}).",
                "Precio mitigando el imbalance.",
            ],
            entry=EntryZone(low=gap.bottom, high=gap.top),
            stop_loss=stop,
            take_profit=target,
            metadata={"gap_top": gap.top, "gap_bottom": gap.bottom},
        )
