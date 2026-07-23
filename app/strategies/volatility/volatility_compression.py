"""Volatility Compression: squeeze + liberación — operar la salida.

Supuesto: compresión extrema del rango (ATR corto ≪ largo + consolidación)
precede expansión; la señal solo se emite en la vela que cierra fuera del
rango comprimido, con objetivo por movimiento medido. Limitación: no
anticipa la dirección — espera la liberación.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import analyze_structure, expansion_ratio
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around
from app.strategies.utils import body_ratio, clamp01


class VolatilityCompression(QuantStrategy):
    """Liberación de un squeeze de volatilidad con movimiento medido."""

    name = "volatility_compression"
    version = "1.0"
    category: ClassVar[str] = "volatility"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("compression",)
    preferred_volatility: ClassVar[tuple[str, ...]] = ("low", "normal")
    default_parameters: ClassVar[dict[str, Any]] = {
        "max_compression_ratio": 0.7,
        "measured_move": 1.0,
        "confirmations": ["volume"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Squeeze vigente y cierre de liberación fuera del rango.

        La compresión y el rango se miden EXCLUYENDO la vela actual: la vela
        de liberación ya expande el ATR y rompería su propia condición.
        """
        atr = await self.atr_or_none(ctx)
        if atr is None or atr <= 0 or len(candles) < 30:
            return None
        prior = candles[:-1]
        expansion = expansion_ratio(prior)
        structure = analyze_structure(prior)
        if expansion is None or structure is None:
            return None
        if expansion > self.fparam("max_compression_ratio") or not structure.consolidation:
            return None
        last = candles[-1]
        height = structure.range_high - structure.range_low
        if height <= 0:
            return None

        if last.close > structure.range_high:
            direction = Direction.LONG
            stop = structure.range_low
            target = last.close + height * self.fparam("measured_move")
        elif last.close < structure.range_low:
            direction = Direction.SHORT
            stop = structure.range_high
            target = last.close - height * self.fparam("measured_move")
        else:
            return None  # sigue comprimido: sin liberación no hay señal

        compression = 1.0 - expansion  # más compresión = más energía acumulada

        return Assessment(
            direction=direction,
            quality=clamp01(0.4 + compression),
            strength=body_ratio(last),
            context_fit=0.9,
            probability=clamp01(0.45 + compression * 0.3),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Squeeze de volatilidad (ATR corto/largo {expansion:.2f} ≤ "
                f"{self.fparam('max_compression_ratio'):.2f}) en consolidación.",
                f"Liberación: cierre fuera del rango "
                f"[{structure.range_low:.4f}, {structure.range_high:.4f}].",
                "Objetivo por movimiento medido del rango.",
            ],
            entry=entry_zone_around(last.close, atr),
            stop_loss=stop,
            take_profit=target,
            metadata={"compression_ratio": round(expansion, 3), "range_height": round(height, 6)},
        )
