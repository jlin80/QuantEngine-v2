"""Range Breakout: ruptura validada del rango previo, penalizando falsas.

Supuesto: la ruptura del rango de N velas con volumen y cierre más allá del
nivel continúa; falsas rupturas recientes en la misma dirección reducen la
probabilidad. Limitación: sin volumen la ruptura no valida (se descarta).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from app.analytics.indicators import detect_breakout
from app.analytics.indicators.structure import fake_breakouts
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.market.models import Candle
from app.strategies.base import Assessment, QuantStrategy
from app.strategies.shared import entry_zone_around, protective_stop
from app.strategies.utils import body_ratio, clamp01


class RangeBreakout(QuantStrategy):
    """Ruptura del rango previo con validación por volumen y cierre."""

    name = "range_breakout"
    version = "1.0"
    category: ClassVar[str] = "breakout"
    preferred_regimes: ClassVar[tuple[str, ...]] = ("breakout", "expansion", "trending")
    default_parameters: ClassVar[dict[str, Any]] = {
        "range_lookback": 20,
        "min_volume_ratio": 1.2,
        "fake_scan": 5,
        "stop_buffer_atr": 0.5,
        "target_range_mult": 1.0,
        "confirmations": ["volume", "delta"],
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Breakout válido en la última vela, ajustado por falsas recientes."""
        atr = await self.atr_or_none(ctx)
        if atr is None or atr <= 0:
            return None
        lookback = self.iparam("range_lookback")
        breakout = detect_breakout(
            candles,
            lookback=lookback,
            min_volume_ratio=self.fparam("min_volume_ratio"),
        )
        if breakout is None or not breakout.valid:
            return None

        prior = candles[-lookback - 1 : -1]
        height = max(c.high for c in prior) - min(c.low for c in prior)
        fakes = [
            d
            for d in fake_breakouts(candles[:-1], lookback=lookback, scan=self.iparam("fake_scan"))
            if d == breakout.direction
        ]
        last = candles[-1]
        direction = Direction.LONG if breakout.direction == "up" else Direction.SHORT
        stop = protective_stop(
            direction, breakout.level, atr, buffer_atr=self.fparam("stop_buffer_atr")
        )
        # Proyección desde el cierre: el objetivo nunca queda detrás de la
        # entrada aunque la vela de ruptura ya haya recorrido parte del rango.
        extension = height * self.fparam("target_range_mult")
        target = last.close + extension if direction is Direction.LONG else last.close - extension

        warnings: list[str] = []
        if fakes:
            warnings.append(f"{len(fakes)} falsa(s) ruptura(s) reciente(s) en la misma dirección.")

        return Assessment(
            direction=direction,
            quality=clamp01(breakout.volume_ratio / 2.0),
            strength=body_ratio(last),
            context_fit=0.85,
            probability=clamp01(0.55 - 0.1 * len(fakes)),
            risk=self.volatility_risk(ctx),
            reasons=[
                f"Ruptura {breakout.direction} del rango de {lookback} velas "
                f"sobre {breakout.level:.4f}.",
                f"Validada por volumen ({breakout.volume_ratio:.2f}x) y cierre fuera.",
                "Objetivo por proyección del alto del rango.",
            ],
            warnings=warnings,
            entry=entry_zone_around(last.close, atr),
            stop_loss=stop,
            take_profit=target,
            metadata={
                "level": breakout.level,
                "volume_ratio": round(breakout.volume_ratio, 2),
                "recent_fakes": len(fakes),
            },
        )
