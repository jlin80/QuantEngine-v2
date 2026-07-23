"""Break Of Structure: continuación en el retest del nivel roto.

Supuesto: tras un BOS, el swing roto cambia de polaridad (soporte↔resistencia)
y el retest ofrece continuación con riesgo definido. Limitación: sin
desplazamiento el BOS puede ser barrido de liquidez (la calidad lo pondera).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import SMCAnalysis
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import clamp01


class BreakOfStructure(QuantStrategy):
    """Retest del nivel roto por el último BOS."""

    name = "bos"
    version = "1.0"
    category: ClassVar[str] = "smc"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("trending", "breakout")
    default_parameters: ClassVar[dict[str, Any]] = {
        "recent_bars": 8,
        "retest_atr": 0.75,
        "stop_buffer_atr": 0.75,
        "risk_reward": 2.0,
        "confirmations": ["volume"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Último BOS reciente con el precio retesteando el nivel."""
        smc = await ctx.features.get_object("smc", ctx.symbol, timeframe=self.timeframe.value)
        if not isinstance(smc, SMCAnalysis) or not smc.breaks:
            return None
        cutoff = len(candles) - self.iparam("recent_bars")
        bos = [b for b in smc.breaks if b.kind == "BOS" and b.index >= cutoff]
        if not bos:
            return None
        event = bos[-1]
        last = candles[-1]
        if abs(last.close - event.level) > self.fparam("retest_atr") * smc.atr:
            return None

        direction = Direction.LONG if event.direction == "up" else Direction.SHORT
        stop = protective_stop(
            direction, event.level, smc.atr, buffer_atr=self.fparam("stop_buffer_atr")
        )
        entry = entry_zone_around(last.close, smc.atr)
        entry_mid = (entry.low + entry.high) / 2.0

        return Assessment(
            direction=direction,
            quality=clamp01(0.4 + event.displacement * 0.15),
            strength=clamp01(event.displacement / 2.0),
            context_fit=0.9,
            probability=0.55,
            risk=self.volatility_risk(ctx),
            reasons=[
                f"BOS {event.direction} confirmado sobre {event.level:.4f} "
                f"(desplazamiento {event.displacement:.1f} ATR).",
                "Precio retesteando el nivel roto (cambio de polaridad).",
            ],
            entry=entry,
            stop_loss=stop,
            take_profit=target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward")),
            metadata={"level": event.level, "displacement": round(event.displacement, 2)},
        )
