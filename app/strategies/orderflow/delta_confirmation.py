"""Delta Confirmation: agresión dominante alineada con el precio.

Supuesto: cuando el delta agresor domina claramente un lado Y el precio
acompaña, la iniciativa tiene continuidad de corto plazo. Limitación: si hay
absorción contraria la señal pierde valor (se emite advertencia).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import OrderFlowSnapshot
from app.engine.events import DeltaCalculated
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import body_ratio, clamp01, is_bearish, is_bullish


class DeltaConfirmation(QuantStrategy):
    """Momentum de corto plazo con delta agresor dominante."""

    name = "delta_confirmation"
    version = "1.0"
    category: ClassVar[str] = "orderflow"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("trending", "breakout", "expansion")
    default_parameters: ClassVar[dict[str, Any]] = {
        "min_aggression": 0.65,
        "stop_atr": 1.0,
        "risk_reward": 1.5,
        "signal_ttl_seconds": 180.0,
        "confirmations": ["volume", "spread"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Agresión dominante + vela y desplazamiento en el mismo sentido."""
        flow = await ctx.features.get_object("orderflow", ctx.symbol)
        atr = await self.atr_or_none(ctx)
        if not isinstance(flow, OrderFlowSnapshot) or atr is None or atr <= 0:
            return None
        last = candles[-1]
        threshold = self.fparam("min_aggression")

        if flow.aggression_ratio >= threshold and is_bullish(last) and flow.price_change_pct > 0:
            direction = Direction.LONG
        elif (
            flow.aggression_ratio <= 1.0 - threshold
            and is_bearish(last)
            and flow.price_change_pct < 0
        ):
            direction = Direction.SHORT
        else:
            return None

        ctx.detections.append(
            DeltaCalculated(
                source=self.name,
                symbol=ctx.symbol,
                delta=flow.delta,
                aggression_ratio=flow.aggression_ratio,
            )
        )
        warnings: list[str] = []
        absorbed_against = (direction is Direction.LONG and flow.absorption == "bearish") or (
            direction is Direction.SHORT and flow.absorption == "bullish"
        )
        if absorbed_against:
            warnings.append("Posible absorción en contra de la agresión.")
        if flow.exhaustion:
            warnings.append(f"Signos de agotamiento del lado {flow.exhaustion}.")

        stop_ref = last.close - atr * self.fparam("stop_atr") * (
            1 if direction is Direction.LONG else -1
        )
        stop = protective_stop(direction, stop_ref, atr, buffer_atr=0.0)
        entry = entry_zone_around(last.close, atr)
        entry_mid = (entry.low + entry.high) / 2.0
        dominance = abs(2.0 * flow.aggression_ratio - 1.0)

        return Assessment(
            direction=direction,
            quality=clamp01(dominance * 1.4),
            strength=body_ratio(last),
            context_fit=0.8 if not absorbed_against else 0.4,
            probability=clamp01(0.4 + dominance * 0.3 - (0.15 if warnings else 0.0)),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Delta {'positivo' if flow.delta > 0 else 'negativo'} ({flow.delta:+.2f}) "
                f"con agresión {flow.aggression_ratio:.0%}.",
                f"Precio acompañando ({flow.price_change_pct:+.3f}%).",
            ],
            warnings=warnings,
            entry=entry,
            stop_loss=stop,
            take_profit=target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward")),
            metadata={"delta": round(flow.delta, 4), "aggression": flow.aggression_ratio},
        )
