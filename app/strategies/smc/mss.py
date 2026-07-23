"""Market Structure Shift: CHOCH con desplazamiento — reversión confirmada.

Supuesto: un cambio de carácter ejecutado con vela de desplazamiento (cuerpo
≥ N ATRs) es un giro institucional, no un barrido. Más calidad y objetivo más
ambicioso que el CHOCH simple. Limitación: ocurre pocas veces; su valor está
en el consenso con las demás señales de reversión.
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


class MarketStructureShift(QuantStrategy):
    """Reversión sobre un MSS (CHOCH con desplazamiento fuerte)."""

    name = "mss"
    version = "1.0"
    category: ClassVar[str] = "smc"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("reversal", "expansion")
    default_parameters: ClassVar[dict[str, Any]] = {
        "recent_bars": 6,
        "origin_window": 12,
        "stop_buffer_atr": 0.5,
        "risk_reward": 2.5,
        "confirmations": ["delta"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Último MSS reciente (CHOCH con displacement)."""
        smc = await ctx.features.get_object("smc", ctx.symbol, timeframe=self.timeframe.value)
        if not isinstance(smc, SMCAnalysis) or not smc.breaks:
            return None
        cutoff = len(candles) - self.iparam("recent_bars")
        shifts = [b for b in smc.breaks if b.mss and b.index >= cutoff]
        if not shifts:
            return None
        event = shifts[-1]
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
        zone = smc.premium_discount
        if zone is not None:
            target = zone.range_high if direction is Direction.LONG else zone.range_low
        else:
            target = target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward"))

        return Assessment(
            direction=direction,
            quality=clamp01(0.55 + event.displacement * 0.1),
            strength=clamp01(event.displacement / 2.5),
            context_fit=0.85,
            probability=0.55,
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Market Structure Shift {event.direction}: CHOCH con desplazamiento "
                f"de {event.displacement:.1f} ATR sobre {event.level:.4f}.",
                "Giro estructural confirmado por la vela de desplazamiento.",
            ],
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            metadata={"level": event.level, "displacement": round(event.displacement, 2)},
        )
