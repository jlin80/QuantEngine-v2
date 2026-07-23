"""Liquidity Sweep: reversión tras barrer un pool de liquidez con rechazo.

Supuesto: una mecha que barre stops (pool de equal highs/lows o extremo del
rango) y cierra de vuelta indica caza de liquidez institucional; el precio
suele revertir hacia el lado opuesto. Limitación: en tendencias con
desplazamiento la "reclamación" puede fallar (el filtro de continuación y el
consenso mitigan).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import LiquidityMap
from app.engine.events import LiquidityDetected
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import clamp01


class LiquiditySweep(QuantStrategy):
    """Fade del barrido de un pool de liquidez con rechazo confirmado."""

    name = "liquidity_sweep"
    version = "1.0"
    category: ClassVar[str] = "smc"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("ranging", "reversal")
    default_parameters: ClassVar[dict[str, Any]] = {
        "recent_bars": 3,
        "stop_buffer_atr": 0.25,
        "risk_reward": 2.0,
        "confirmations": ["delta", "cvd"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Último stop hunt con rechazo dentro de las velas recientes."""
        liquidity = await ctx.features.get_object(
            "liquidity", ctx.symbol, timeframe=self.timeframe.value
        )
        atr = await self.atr_or_none(ctx)
        if not isinstance(liquidity, LiquidityMap) or atr is None or atr <= 0:
            return None

        cutoff = len(candles) - self.iparam("recent_bars")
        hunts = [h for h in liquidity.hunts if h.rejection and h.index >= cutoff]
        if not hunts:
            return None
        hunt = hunts[-1]
        sweep_candle = candles[hunt.index]
        last = candles[-1]

        if hunt.pool.kind == "low":
            direction = Direction.LONG
            reference = sweep_candle.low
            opposite = liquidity.nearest_pool(last.close, "high")
        else:
            direction = Direction.SHORT
            reference = sweep_candle.high
            opposite = liquidity.nearest_pool(last.close, "low")

        ctx.detections.append(
            LiquidityDetected(
                source=self.name,
                symbol=ctx.symbol,
                kind="stop_hunt",
                level=hunt.pool.level,
                detail=f"barrido de {hunt.pool.kind}s con rechazo (mecha {hunt.wick_ratio:.0%})",
            )
        )
        stop = protective_stop(direction, reference, atr, buffer_atr=self.fparam("stop_buffer_atr"))
        entry = entry_zone_around(last.close, atr)
        entry_mid = (entry.low + entry.high) / 2.0
        target = (
            opposite.level
            if opposite is not None
            else target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward"))
        )

        return Assessment(
            direction=direction,
            quality=clamp01(0.35 + 0.15 * hunt.pool.strength + 0.3 * hunt.wick_ratio),
            strength=hunt.wick_ratio,
            context_fit=0.9 if not hunt.pool.internal else 0.6,
            probability=clamp01(0.45 + 0.1 * hunt.pool.strength),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Liquidity sweep detectado sobre pool de {hunt.pool.kind}s "
                f"en {hunt.pool.level:.4f} (fuerza {hunt.pool.strength}).",
                f"Rechazo con mecha del {hunt.wick_ratio:.0%} y cierre de vuelta.",
                "Liquidez externa barrida." if not hunt.pool.internal else "Liquidez interna.",
            ],
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            metadata={"pool_level": hunt.pool.level, "pool_strength": hunt.pool.strength},
        )
