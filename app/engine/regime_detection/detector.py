"""Detección de régimen de mercado a partir de velas cerradas.

Heurísticas deliberadamente simples y documentadas (sin ML en esta fase):

* **Efficiency Ratio** (Kaufman): ``|Δneto| / Σ|Δbarra|`` — alto = tendencia.
* **Ratio de ATR corto/largo** — expansión vs compresión de volatilidad.
* **Ruptura de rango**: cierre fuera del máximo/mínimo de N velas previas.
* **Reversión**: tendencia neta de la ventana contradicha por el último
  tramo de velas.

El régimen primario se elige por prioridad (breakout > reversal > trending >
ranging) y la volatilidad/expansión se reportan como etiquetas adicionales.
"""

import itertools
import logging

from app.config.settings import QuantRegimeSettings
from app.engine.models import Regime, RegimeState
from app.market.models import Candle, Timeframe
from app.market.services import MarketDataService
from app.utils.time import utc_now


class RegimeDetector:
    """Classifies the market regime per symbol from closed candles.

    Args:
        market: API de datos agnóstica del broker.
        settings: Umbrales del detector.
    """

    def __init__(self, market: MarketDataService, settings: QuantRegimeSettings) -> None:
        self._market = market
        self._settings = settings
        self._timeframe = Timeframe(settings.timeframe)
        self._last: dict[str, RegimeState] = {}
        self._log = logging.getLogger("app.engine.regime")

    def last_state(self, symbol: str) -> RegimeState | None:
        """Última clasificación conocida (sin recalcular)."""
        return self._last.get(symbol.upper())

    def detect(self, symbol: str) -> RegimeState:
        """Classify the current regime for a symbol.

        Args:
            symbol: Símbolo interno.

        Returns:
            Estado de régimen (``UNKNOWN`` si no hay velas suficientes).
        """
        symbol = symbol.upper()
        candles = self._market.get_candles(symbol, self._timeframe, self._settings.lookback)
        state = self._classify(symbol, candles)
        self._last[symbol] = state
        return state

    def _classify(self, symbol: str, candles: list[Candle]) -> RegimeState:
        """Core classification from a candle window."""
        now = utc_now()
        if len(candles) < 10:
            return RegimeState(
                symbol=symbol,
                primary=Regime.UNKNOWN,
                metrics={"candles": float(len(candles))},
                detected_at=now,
            )
        closes = [candle.close for candle in candles]
        highs = [candle.high for candle in candles]
        lows = [candle.low for candle in candles]

        net_change = abs(closes[-1] - closes[0])
        path = sum(abs(b - a) for a, b in itertools.pairwise(closes))
        efficiency = net_change / path if path > 0 else 0.0

        ranges = [h - low for h, low in zip(highs, lows, strict=False)]
        short = ranges[-5:]
        atr_short = sum(short) / len(short)
        atr_long = sum(ranges) / len(ranges)
        vol_ratio = atr_short / atr_long if atr_long > 0 else 1.0

        lookback = min(self._settings.breakout_lookback, len(candles) - 1)
        prior_high = max(highs[-lookback - 1 : -1])
        prior_low = min(lows[-lookback - 1 : -1])
        breakout_up = closes[-1] > prior_high
        breakout_down = closes[-1] < prior_low

        half = len(closes) // 2
        first_leg = closes[half] - closes[0]
        last_leg = closes[-1] - closes[half]
        reversal = (
            efficiency < self._settings.trending_efficiency
            and abs(first_leg) > atr_long
            and abs(last_leg) > atr_long * 0.5
            and (first_leg > 0) != (last_leg > 0)
        )

        tags: list[Regime] = []
        if vol_ratio >= self._settings.expansion_ratio:
            tags.append(Regime.EXPANSION)
            tags.append(Regime.HIGH_VOLATILITY)
        elif vol_ratio <= self._settings.compression_ratio:
            tags.append(Regime.COMPRESSION)
            tags.append(Regime.LOW_VOLATILITY)

        if breakout_up or breakout_down:
            primary = Regime.BREAKOUT
        elif reversal:
            primary = Regime.REVERSAL
        elif efficiency >= self._settings.trending_efficiency:
            primary = Regime.TRENDING
        else:
            primary = Regime.RANGING

        return RegimeState(
            symbol=symbol,
            primary=primary,
            tags=tuple(tags),
            metrics={
                "efficiency_ratio": round(efficiency, 4),
                "vol_ratio": round(vol_ratio, 4),
                "atr_short": round(atr_short, 8),
                "atr_long": round(atr_long, 8),
                "breakout_up": float(breakout_up),
                "breakout_down": float(breakout_down),
                "candles": float(len(candles)),
            },
            detected_at=now,
        )
