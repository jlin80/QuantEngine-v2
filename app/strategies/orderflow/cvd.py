"""CVD (Cumulative Volume Delta): acumulación/distribución silenciosa.

Supuesto: CVD subiendo con precio plano = compradores absorbidos por límites
que eventualmente ceden (acumulación); el espejo es distribución. Limitación:
requiere flujo de trades real; con pocos trades no opina.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import OrderFlowSnapshot
from app.engine.events import CVDCalculated
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop, target_from_rr
from app.strategies.utils import clamp01


class CumulativeVolumeDelta(QuantStrategy):
    """Divergencia CVD/precio: flujo direccional sin desplazamiento aún."""

    name = "cvd"
    version = "1.0"
    category: ClassVar[str] = "orderflow"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("ranging", "compression")
    default_parameters: ClassVar[dict[str, Any]] = {
        "min_cvd_slope": 0.3,
        "max_price_drift_pct": 0.05,
        "stop_atr": 1.2,
        "risk_reward": 2.0,
        "confirmations": ["volume"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """CVD con pendiente clara mientras el precio sigue plano."""
        flow = await ctx.features.get_object("orderflow", ctx.symbol)
        atr = await self.atr_or_none(ctx)
        if not isinstance(flow, OrderFlowSnapshot) or atr is None or atr <= 0:
            return None
        min_slope = self.fparam("min_cvd_slope")
        if abs(flow.price_change_pct) > self.fparam("max_price_drift_pct"):
            return None
        if flow.cvd_slope >= min_slope:
            direction = Direction.LONG
            mode = "acumulación"
        elif flow.cvd_slope <= -min_slope:
            direction = Direction.SHORT
            mode = "distribución"
        else:
            return None

        ctx.detections.append(
            CVDCalculated(
                source=self.name,
                symbol=ctx.symbol,
                cvd=flow.cvd_series[-1] if flow.cvd_series else 0.0,
                slope=flow.cvd_slope,
            )
        )
        last = candles[-1]
        stop_ref = last.close - atr * self.fparam("stop_atr") * (
            1 if direction is Direction.LONG else -1
        )
        stop = protective_stop(direction, stop_ref, atr, buffer_atr=0.0)
        entry = entry_zone_around(last.close, atr)
        entry_mid = (entry.low + entry.high) / 2.0

        return Assessment(
            direction=direction,
            quality=clamp01(abs(flow.cvd_slope)),
            strength=clamp01(flow.large_trade_ratio * 1.5),
            context_fit=0.8,
            probability=clamp01(0.4 + abs(flow.cvd_slope) * 0.25),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"CVD {'creciente' if flow.cvd_slope > 0 else 'decreciente'} "
                f"({flow.cvd_slope:+.2f}) con precio plano "
                f"({flow.price_change_pct:+.3f}%): {mode}.",
                f"Participación de trades grandes: {flow.large_trade_ratio:.0%}.",
            ],
            entry=entry,
            stop_loss=stop,
            take_profit=target_from_rr(entry_mid, stop, risk_reward=self.fparam("risk_reward")),
            metadata={"cvd_slope": round(flow.cvd_slope, 4), "mode": mode},
        )
