"""Order Book Imbalance: desequilibrio del libro con presión coincidente.

Supuesto: imbalance y presión ponderada apuntando al mismo lado anticipan un
micro-movimiento en esa dirección. Limitación: señal de vida corta (TTL
reducido) y dependiente de tener libro de órdenes en vivo.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import clamp01


class OrderBookImbalance(QuantStrategy):
    """Micro-señal por imbalance + presión del libro alineados."""

    name = "orderbook_imbalance"
    version = "1.0"
    category: ClassVar[str] = "orderflow"
    preferred_regimes: ClassVar[tuple[str, ...]] = ()
    default_parameters: ClassVar[dict[str, Any]] = {
        "min_imbalance": 0.25,
        "min_pressure": 0.2,
        "stop_atr": 0.6,
        "risk_reward": 1.5,
        "signal_ttl_seconds": 120.0,
        "confirmations": ["delta", "spread"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Imbalance y presión superando umbrales en el mismo sentido."""
        imbalance = await ctx.features.get("imbalance", ctx.symbol)
        pressure = await ctx.features.get("book_pressure", ctx.symbol)
        atr = await self.atr_or_none(ctx)
        if imbalance is None or pressure is None or atr is None or atr <= 0:
            return None
        min_imb = self.fparam("min_imbalance")
        min_pre = self.fparam("min_pressure")

        if imbalance >= min_imb and pressure >= min_pre:
            direction = Direction.LONG
        elif imbalance <= -min_imb and pressure <= -min_pre:
            direction = Direction.SHORT
        else:
            return None

        last = candles[-1]
        stop_ref = last.close - atr * self.fparam("stop_atr") * (
            1 if direction is Direction.LONG else -1
        )
        stop = protective_stop(direction, stop_ref, atr, buffer_atr=0.0)
        entry = entry_zone_around(last.close, atr, width_atr=0.1)
        entry_mid = (entry.low + entry.high) / 2.0

        return Assessment(
            direction=direction,
            quality=clamp01(abs(imbalance) / 0.5),
            strength=clamp01(abs(pressure) / 0.5),
            context_fit=0.7,
            probability=clamp01(0.4 + abs(imbalance) * 0.3),
            risk=self.volatility_risk(ctx, base=0.35),
            reasons=[
                f"Imbalance del libro {imbalance:+.2f} "
                f"({'compradora' if imbalance > 0 else 'vendedora'}).",
                f"Presión ponderada coincidente ({pressure:+.2f}).",
            ],
            warnings=["Señal de corto plazo: el libro cambia rápido."],
            entry=entry,
            stop_loss=stop,
            take_profit=target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward")),
            metadata={"imbalance": round(imbalance, 4), "book_pressure": round(pressure, 4)},
        )
