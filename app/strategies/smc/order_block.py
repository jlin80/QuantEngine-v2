"""Order Block: entrada en el retorno a un OB alineado con la estructura.

Supuesto: la última vela contraria antes de un desplazamiento marca órdenes
institucionales sin llenar; el primer retorno a la zona suele reaccionar.
Limitación: un OB "breaker" (polaridad rota) se descarta; los mitigados
pierden calidad.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import SMCAnalysis
from app.engine.events import OrderBlockDetected
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction, EntryZone
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import protective_stop, target_from_rr
from app.strategies.utils import clamp01


class OrderBlock(QuantStrategy):
    """Reacción en un order block fresco alineado con la última ruptura."""

    name = "order_block"
    version = "1.0"
    category: ClassVar[str] = "smc"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("trending", "breakout")
    default_parameters: ClassVar[dict[str, Any]] = {
        "allow_mitigated": False,
        "stop_buffer_atr": 0.5,
        "risk_reward": 2.0,
        "confirmations": ["delta"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Precio interactuando con el OB más reciente alineado."""
        smc = await ctx.features.get_object("smc", ctx.symbol, timeframe=self.timeframe.value)
        if not isinstance(smc, SMCAnalysis) or not smc.breaks:
            return None
        wanted = "bullish" if smc.breaks[-1].direction == "up" else "bearish"
        allow_mitigated = bool(self.parameters["allow_mitigated"])
        blocks = [
            b
            for b in smc.order_blocks
            if b.direction == wanted and not b.breaker and (allow_mitigated or not b.mitigated)
        ]
        if not blocks:
            return None
        block = blocks[-1]
        last = candles[-1]

        if block.direction == "bullish":
            interacting = last.low <= block.top and last.close >= block.bottom
            direction = Direction.LONG
            reference = block.bottom
        else:
            interacting = last.high >= block.bottom and last.close <= block.top
            direction = Direction.SHORT
            reference = block.top
        if not interacting:
            return None

        ctx.detections.append(
            OrderBlockDetected(
                source=self.name,
                symbol=ctx.symbol,
                direction=block.direction,
                top=block.top,
                bottom=block.bottom,
                mitigated=block.mitigated,
            )
        )
        stop = protective_stop(
            direction, reference, smc.atr, buffer_atr=self.fparam("stop_buffer_atr")
        )
        entry_mid = (block.top + block.bottom) / 2.0
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

        return Assessment(
            direction=direction,
            quality=0.7 if not block.mitigated else 0.45,
            strength=clamp01(smc.breaks[-1].displacement / 3.0),
            context_fit=0.9,
            probability=0.55 if not block.mitigated else 0.45,
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Order block {block.direction} en [{block.bottom:.4f}, {block.top:.4f}] "
                f"({'fresco' if not block.mitigated else 'mitigado'}).",
                f"Alineado con la última ruptura estructural ({smc.breaks[-1].kind} "
                f"{smc.breaks[-1].direction}).",
                "Precio interactuando con la zona.",
            ],
            entry=EntryZone(low=block.bottom, high=block.top),
            stop_loss=stop,
            take_profit=target,
            metadata={"block_top": block.top, "block_bottom": block.bottom},
        )
