"""ATR Expansion: la volatilidad despierta, momentum en la dirección del disparo.

Supuesto: cuando el ATR corto supera con claridad al largo y sigue subiendo,
el mercado inicia un movimiento direccional; la vela dominante marca el lado.
Limitación: la expansión también ocurre en pánicos bilaterales (el cuerpo
mínimo filtra parte).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import body_ratio, clamp01, is_bullish


class ATRExpansion(QuantStrategy):
    """Ruptura de volatilidad: expansión del ATR con vela dominante."""

    name = "atr_expansion"
    version = "1.0"
    category: ClassVar[str] = "volatility"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("expansion", "breakout", "trending")
    preferred_volatility: ClassVar[tuple[str, ...]] = ("normal", "high")
    default_parameters: ClassVar[dict[str, Any]] = {
        "min_expansion_ratio": 1.4,
        "min_body_ratio": 0.5,
        "stop_atr": 1.0,
        "risk_reward": 1.5,
        "confirmations": ["volume"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Expansión del ATR con pendiente positiva y vela dominante."""
        expansion = await ctx.features.get(
            "atr_expansion", ctx.symbol, timeframe=self.timeframe.value
        )
        slope = await ctx.features.get("atr_slope", ctx.symbol, timeframe=self.timeframe.value)
        atr = await self.atr_or_none(ctx)
        if expansion is None or atr is None or atr <= 0:
            return None
        if expansion < self.fparam("min_expansion_ratio") or (slope is not None and slope <= 0):
            return None
        last = candles[-1]
        body = body_ratio(last)
        if body < self.fparam("min_body_ratio"):
            return None

        direction = Direction.LONG if is_bullish(last) else Direction.SHORT
        stop_ref = last.close - atr * self.fparam("stop_atr") * (
            1 if direction is Direction.LONG else -1
        )
        stop = protective_stop(direction, stop_ref, atr, buffer_atr=0.0)
        entry = entry_zone_around(last.close, atr)
        entry_mid = (entry.low + entry.high) / 2.0

        return Assessment(
            direction=direction,
            quality=clamp01((expansion - 1.0) / 1.0),
            strength=body,
            context_fit=0.85,
            probability=clamp01(0.4 + (expansion - 1.0) * 0.2),
            risk=self.volatility_risk(ctx),
            reasons=[
                (
                    f"Expansión de volatilidad: ATR corto/largo {expansion:.2f} "
                    f"(pendiente {slope:+.2f}%/bar)."
                    if slope is not None
                    else f"Expansión de volatilidad: ATR corto/largo {expansion:.2f}."
                ),
                f"Vela dominante ({body:.0%} de cuerpo) marcando la dirección.",
            ],
            entry=entry,
            stop_loss=stop,
            take_profit=target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward")),
            metadata={"expansion_ratio": round(expansion, 3)},
        )
