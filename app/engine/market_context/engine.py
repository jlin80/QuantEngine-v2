"""Market Context Engine: el estado del mercado que todos consumen.

Responde: ¿tendencia o lateral? ¿volatilidad alta/baja? ¿qué sesión está
activa? ¿spread elevado? ¿volumen suficiente? ¿noticias próximas? ¿los datos
son frescos y la conexión está viva?
"""

import logging
from datetime import datetime

from app.config.settings import QuantContextSettings
from app.engine.feature_store import FeatureStore
from app.engine.models import MarketContext, VolatilityState
from app.engine.regime_detection import RegimeDetector
from app.market.models import Timeframe
from app.market.services import MarketDataService
from app.utils.time import utc_now


class MarketContextEngine:
    """Builds the :class:`MarketContext` snapshot per symbol.

    Args:
        market: API de datos agnóstica del broker.
        features: Feature Store compartido.
        regime: Detector de régimen.
        settings: Umbrales y sesiones.
    """

    def __init__(
        self,
        market: MarketDataService,
        features: FeatureStore,
        regime: RegimeDetector,
        settings: QuantContextSettings,
    ) -> None:
        self._market = market
        self._features = features
        self._regime = regime
        self._settings = settings
        self._timeframe = Timeframe(settings.context_timeframe)
        self._blackouts = _parse_blackouts(settings.news_blackouts)
        self._log = logging.getLogger("app.engine.context")

    async def build(self, symbol: str) -> MarketContext:
        """Compose the full market context for a symbol.

        Args:
            symbol: Símbolo interno.

        Returns:
            Contexto listo para estrategias, filtros y decisión.
        """
        symbol = symbol.upper()
        now = utc_now()
        tf = self._timeframe.value

        atr = await self._features.get("atr", symbol, timeframe=tf)
        atr_pct = await self._features.get("atr_pct", symbol, timeframe=tf)
        spread_bps = await self._features.get("spread_bps", symbol)
        volume = await self._features.get("volume", symbol, timeframe=tf)
        last_price = await self._features.get("last_price", symbol)

        volatility = VolatilityState.NORMAL
        if atr_pct is not None:
            # Umbrales por símbolo: la escala de ATR% no es comparable entre
            # activos (oro ~0.038 % vs ETH ~0.068 % de mediana en 1m), así que
            # un par único de umbrales dejaba la variable constante.
            if atr_pct >= self._settings.atr_pct_high_for(symbol):
                volatility = VolatilityState.HIGH
            elif atr_pct <= self._settings.atr_pct_low_for(symbol):
                volatility = VolatilityState.LOW

        return MarketContext(
            symbol=symbol,
            generated_at=now,
            regime=self._regime.detect(symbol),
            sessions=self.active_sessions(now),
            volatility=volatility,
            atr=atr,
            atr_pct=atr_pct,
            spread_bps=spread_bps,
            spread_elevated=(spread_bps is not None and spread_bps > self._settings.max_spread_bps),
            volume_recent=volume,
            volume_sufficient=(volume is None or volume >= self._settings.min_volume),
            news_blackout=self.in_news_blackout(now),
            data_quality=self._data_quality(symbol, now),
            last_price=last_price,
        )

    def active_sessions(self, moment: datetime) -> tuple[str, ...]:
        """Active sessions for a UTC instant (pueden solaparse)."""
        hour = moment.hour
        active = [
            name
            for name, (start, end) in self._settings.session_hours.items()
            if (start <= hour < end) or (start > end and (hour >= start or hour < end))
        ]
        return tuple(active)

    def in_news_blackout(self, moment: datetime) -> bool:
        """Whether a configured news window is active."""
        return any(start <= moment <= end for start, end in self._blackouts)

    def _data_quality(self, symbol: str, now: datetime) -> float:
        """Data quality 0-1: conexión viva + frescura del último dato."""
        state = self._market.get_market_state(symbol)
        quality = 1.0
        if not state.connected:
            quality -= 0.5
        staleness = state.staleness_seconds
        if staleness is None:
            return 0.0
        if staleness > self._settings.stale_data_seconds:
            # Penaliza linealmente hasta quedar en 0 al triple del umbral.
            over = staleness / self._settings.stale_data_seconds
            quality -= min(0.5, (over - 1.0) * 0.25)
        return max(0.0, min(1.0, quality))


def _parse_blackouts(raw: list[tuple[str, str]]) -> list[tuple[datetime, datetime]]:
    """Parse configured ISO blackout windows (invalid entries ignored)."""
    windows: list[tuple[datetime, datetime]] = []
    for start_raw, end_raw in raw:
        try:
            start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start < end:
            windows.append((start, end))
    return windows
