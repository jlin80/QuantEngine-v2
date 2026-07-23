"""Change Of Character: reversión temprana tras romper contra la tendencia.

Supuesto: el primer cierre contra la estructura vigente delata un cambio de
carácter; entrada temprana con probabilidad moderada. Limitación: es la más
anticipada de las señales SMC — exige confirmación de order flow.
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


class ChangeOfCharacter(QuantStrategy):
    """Entrada temprana tras un CHOCH reciente."""

    name = "choch"
    version = "1.0"
    category: ClassVar[str] = "smc"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("reversal", "ranging")
    default_parameters: ClassVar[dict[str, Any]] = {
        "recent_bars": 5,
        "origin_window": 10,
        "stop_buffer_atr": 0.5,
        "risk_reward": 1.5,
        "confirmations": ["delta", "cvd"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Último CHOCH reciente (sin exigir MSS)."""
        smc = await ctx.features.get_object("smc", ctx.symbol, timeframe=self.timeframe.value)
        if not isinstance(smc, SMCAnalysis) or not smc.breaks:
            return None
        cutoff = len(candles) - self.iparam("recent_bars")
        choch = [b for b in smc.breaks if b.kind == "CHOCH" and b.index >= cutoff]
        if not choch:
            return None
        event = choch[-1]
        last = candles[-1]
        direction = Direction.LONG if event.direction == "up" else Direction.SHORT

        window = candles[-self.iparam("origin_window") :]
        if direction is Direction.LONG:
            origin = min(c.low for c in window)
        else:
            origin = max(c.high for c in window)
        stop = protective_stop(
            direction, origin, smc.atr, buffer_atr=self.fparam("stop_buffer_atr")
        )
        entry = entry_zone_around(last.close, smc.atr)
        entry_mid = (entry.low + entry.high) / 2.0
        if smc.premium_discount is not None:
            target = smc.premium_discount.equilibrium
        else:
            target = target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward"))

        return Assessment(
            direction=direction,
            quality=clamp01(0.35 + event.displacement * 0.15),
            strength=clamp01(event.displacement / 2.0),
            context_fit=0.7,
            probability=0.45,
            risk=self.volatility_risk(ctx, base=0.4),
            reasons=[
                f"CHOCH {event.direction} sobre {event.level:.4f}: primer cierre "
                "contra la estructura vigente.",
                f"Desplazamiento {event.displacement:.1f} ATR.",
                "Reversión temprana: objetivo en el equilibrium del rango.",
            ],
            warnings=["Señal anticipada: exige confirmación de order flow."],
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            metadata={"level": event.level, "displacement": round(event.displacement, 2)},
        )
