"""Servicio que alimenta y valida el pronóstico de régimen (Bloque 4).

Separado del motor a propósito: el motor es aritmética pura sobre frecuencias y
se puede usar sobre histórico en un backtest sin arrastrar servicios; esto es lo
que le da un reloj, un mercado y una cadencia.
"""

import asyncio
import contextlib
import logging
from typing import Any

from app.config.settings import QuantRegimeForecastSettings
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.engine.events.events import RegimeForecastUpdated
from app.engine.models import VolatilityState
from app.engine.regime_detection import RegimeDetector
from app.engine.regime_forecast.engine import RegimeForecastEngine
from app.engine.regime_forecast.models import RegimeForecast
from app.market.models import Timeframe
from app.market.services import MarketDataService


class RegimeForecastService(Service):
    """Keep the forecast engine fed, and score it against reality.

    Args:
        settings: Configuración del pronóstico.
        engine: Motor de frecuencias condicionales.
        detector: Detector de régimen (Fase 3): dice desde dónde se pronostica.
        market: API de datos (velas del timeframe de contexto).
        symbols: Símbolos a seguir.
        timeframe: Marco temporal de las velas usadas.
        bus: Event Bus donde anunciar los pronósticos.
        cycle_seconds: Cadencia del ciclo.
    """

    def __init__(
        self,
        settings: QuantRegimeForecastSettings,
        engine: RegimeForecastEngine,
        detector: RegimeDetector,
        market: MarketDataService,
        symbols: list[str],
        *,
        timeframe: Timeframe = Timeframe.M5,
        bus: EventBus | None = None,
        cycle_seconds: float = 300.0,
        lookback: int = 1_000,
    ) -> None:
        super().__init__("regime_forecast")
        self._settings = settings
        self._engine = engine
        self._detector = detector
        self._market = market
        self._symbols = symbols
        self._timeframe = timeframe
        self._bus = bus
        self._cycle_seconds = cycle_seconds
        self._lookback = lookback
        self._last: dict[str, RegimeForecast] = {}
        self._cycles = 0
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger("app.engine.regime_forecast.service")

    def cycle(self) -> dict[str, RegimeForecast]:
        """Run one full cycle for every tracked symbol.

        Orden deliberado: primero **resolver** los pronósticos pendientes
        (puntuarlos y aprender de ellos), después emitir los nuevos. Al revés,
        el pronóstico recién emitido entraría en su propia validación.

        Returns:
            El pronóstico vigente por símbolo.
        """
        for symbol in self._symbols:
            candles = list(self._market.get_candles(symbol, self._timeframe, self._lookback))
            if not candles:
                continue
            self._engine.resolve(symbol, candles)
            regime = self._detector.detect(symbol)
            volatility = _volatility_of(regime.metrics)
            forecast = self._engine.forecast(symbol, regime.primary, volatility)
            self._last[symbol] = forecast
            self._engine.track(forecast, anchor_index=len(candles) - 1)
        self._cycles += 1
        return dict(self._last)

    def bootstrap(self) -> int:
        """Seed the engine from the history already on disk.

        Sin esto el motor arranca sin muestra y tarda semanas en poder
        pronosticar nada. Es aprendizaje de histórico, no lookahead: cada
        desenlace se clasifica con velas estrictamente posteriores a su ancla.

        Returns:
            Cuántas observaciones se aprendieron.
        """
        learned = 0
        for symbol in self._symbols:
            candles = list(self._market.get_candles(symbol, self._timeframe, self._lookback))
            if len(candles) <= self._settings.horizon_bars * 2:
                continue
            regime = self._detector.detect(symbol)
            learned += self._engine.learn_from_candles(
                candles, regime.primary, _volatility_of(regime.metrics)
            )
        return learned

    async def run_cycle(self) -> dict[str, RegimeForecast]:
        """Run a cycle and announce each observable forecast on the bus."""
        forecasts = self.cycle()
        if self._bus is None:
            return forecasts
        score = self._engine.score()
        for symbol, forecast in forecasts.items():
            if not forecast.observable:
                continue
            await self._bus.publish(
                RegimeForecastUpdated(
                    source="regime_forecast",
                    symbol=symbol,
                    condition=forecast.condition,
                    most_likely=forecast.most_likely,
                    probability=forecast.probabilities.get(forecast.most_likely, 0.0),
                    confidence=forecast.confidence,
                    # El skill viaja con el pronóstico: quien lo lea sabe de
                    # inmediato si este motor ha demostrado valer algo.
                    skill=score.skill,
                )
            )
        return forecasts

    def last(self, symbol: str) -> RegimeForecast | None:
        """Pronóstico vigente de un símbolo."""
        return self._last.get(symbol)

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "cycles": self._cycles,
            "symbols": list(self._symbols),
            "forecasts": {s: f.to_dict() for s, f in sorted(self._last.items())},
            "engine": self._engine.status(),
        }

    async def _loop(self) -> None:
        """Run forecast cycles on their own cadence, forever."""
        while True:
            await asyncio.sleep(self._cycle_seconds)
            try:
                await self.run_cycle()
            except Exception:  # el pronóstico jamás debe tumbar al motor
                self._log.exception("Regime forecast cycle failed")

    async def _on_start(self) -> None:
        if not self._settings.enabled:
            return
        try:
            self.bootstrap()
        except Exception:
            self._log.exception("Regime forecast bootstrap failed")
        self._task = asyncio.create_task(self._loop(), name="regime-forecast")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def _volatility_of(metrics: dict[str, float]) -> VolatilityState:
    """Read the volatility band out of the regime detector's metrics.

    El detector publica su clasificación en `metrics`; si no está, se asume
    ``NORMAL`` en vez de inventar una banda: la condición quedaría mal formada
    y contaminaría las frecuencias de todas las demás.
    """
    raw = metrics.get("volatility_state")
    if isinstance(raw, str):
        try:
            return VolatilityState(raw)
        except ValueError:
            return VolatilityState.NORMAL
    return VolatilityState.NORMAL
