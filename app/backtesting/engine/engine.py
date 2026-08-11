"""Motor de backtest: reproduce el mercado y reutiliza el Execution Engine.

Recorre las velas en orden, instala el reloj de replay y, en cada vela:

1. publica la vela cerrada y recorre su camino intrabar (OHLC) marcando las
   posiciones abiertas, de modo que los stops/objetivos puedan dispararse
   *dentro* de la vela y no solo al cierre;
2. pide una decisión a la :class:`DecisionSource` sobre los datos hasta esa
   vela y, si procede, la pasa por el mismo flujo de entrada del paper trading;
3. muestrea la curva de equity.

El motor **no reimplementa** ejecución, riesgo ni cartera: conduce el mismo
``ExecutionEngine`` de la Fase 5. Assumption declarada: el camino intrabar se
aproxima como ``open → extremo adverso → extremo favorable → close`` según la
dirección de la vela; una reproducción tick-a-tick queda como estructura futura.
"""

import asyncio
from collections.abc import Sequence

from app.backtesting.clock import ReplayClock
from app.backtesting.decisions import DecisionSource
from app.backtesting.market import HistoricalMarket
from app.backtesting.metrics import StatisticsEngine
from app.backtesting.models import BacktestConfig, BacktestResult, EquityPoint
from app.backtesting.simulator import build_execution_stack
from app.config.settings import BacktestingSettings, ExecutionSettings, QuantSettings
from app.engine.feature_store import FeatureStore
from app.engine.market_context import MarketContextEngine
from app.engine.regime_detection import RegimeDetector
from app.execution.models import ExitReason
from app.market.models import Candle


def _ohlc_path(candle: Candle) -> tuple[float, ...]:
    """Return the intrabar price path used to check exits.

    Convención conservadora por dirección de vela: en una vela alcista se asume
    que primero visitó su mínimo (donde caen los stops de largos) y luego su
    máximo; en una bajista, al revés.

    Args:
        candle: Vela a recorrer.

    Returns:
        Secuencia de precios ``open → … → close``.
    """
    if candle.close >= candle.open:
        return (candle.open, candle.low, candle.high, candle.close)
    return (candle.open, candle.high, candle.low, candle.close)


class BacktestEngine:
    """Drive the real Execution Engine over a historical candle series.

    Args:
        settings: Configuración del laboratorio (balance, spread, risk-free).
        execution: Configuración de ejecución (misma que en paper real).
        quant: Configuración del QuantCore. Cuando se pasa (y
            ``settings.market_context_enabled``), el backtest construye el
            **Market Context real** sobre el mismo mercado histórico, de modo
            que el motor vea régimen, sesión y volatilidad igual que en
            producción. Con ``None`` se conserva el camino degradado anterior.
    """

    def __init__(
        self,
        settings: BacktestingSettings,
        execution: ExecutionSettings,
        quant: QuantSettings | None = None,
    ) -> None:
        self._settings = settings
        self._execution = execution
        self._quant = quant

    def _build_context(
        self, market: HistoricalMarket
    ) -> tuple[MarketContextEngine | None, FeatureStore | None]:
        """Build the Market Context Engine over the replayed market, if enabled.

        El contexto se construye **sobre el mismo** ``MarketDataService`` que
        consume el Execution Engine, así que lee exactamente las velas ya
        publicadas por el reloj de replay: no hay lookahead posible por esta vía.

        Devuelve también el Feature Store porque el backtest tiene que
        invalidarlo **vela a vela**: su cache caduca por ``time.monotonic()``
        (tiempo real), y en un backtest miles de velas simuladas caben dentro de
        un TTL de un segundo. Sin invalidar, el contexto se congelaría igual que
        se congeló el reloj el 2026-07-31.
        """
        if self._quant is None or not self._settings.market_context_enabled:
            return None, None
        features = FeatureStore(market.service)
        regime = RegimeDetector(market.service, self._quant.regime)
        context = MarketContextEngine(market.service, features, regime, self._quant.context)
        return context, features

    def run(
        self,
        candles: Sequence[Candle],
        decision_source: DecisionSource,
        config: BacktestConfig,
    ) -> BacktestResult:
        """Run a backtest synchronously.

        Args:
            candles: Serie de velas cerradas, orden cronológico.
            decision_source: Fuente de decisiones por vela.
            config: Configuración del backtest.

        Returns:
            El resultado completo (operaciones, curva de equity, estadística).
        """
        return asyncio.run(self.run_async(candles, decision_source, config))

    async def run_async(
        self,
        candles: Sequence[Candle],
        decision_source: DecisionSource,
        config: BacktestConfig,
    ) -> BacktestResult:
        """Async core of :meth:`run`.

        Args:
            candles: Serie de velas cerradas, orden cronológico.
            decision_source: Fuente de decisiones por vela.
            config: Configuración del backtest.

        Returns:
            El resultado completo del backtest.

        Raises:
            ValueError: Si la serie de velas está vacía.
        """
        if not candles:
            raise ValueError("El backtest necesita al menos una vela")

        execution = self._execution.model_copy(update={"initial_balance": config.initial_balance})
        market = HistoricalMarket()
        context, features = self._build_context(market)
        stack = build_execution_stack(execution, market.service, context=context)
        engine = stack.engine
        clock = ReplayClock(candles[0].start)
        decision_source.reset()

        equity_curve: list[EquityPoint] = []
        with clock.installed():
            for index, candle in enumerate(candles):
                clock.set(candle.end)
                market.push_candle(candle)
                if features is not None:
                    features.invalidate(config.symbol)
                for price in _ohlc_path(candle):
                    market.push_price(
                        config.symbol, price, timestamp=candle.end, spread_bps=config.spread_bps
                    )
                    await engine.manage_once()
                decision = decision_source.decide(config.symbol, candles, index)
                if decision is not None and decision.accepted:
                    await engine.process_decision(decision)
                snapshot = stack.portfolio.snapshot(stack.positions.open_positions)
                equity_curve.append(
                    EquityPoint(
                        timestamp=candle.end,
                        equity=snapshot.equity,
                        balance=snapshot.balance,
                        drawdown_pct=snapshot.drawdown_pct,
                        open_positions=len(stack.positions.open_positions),
                    )
                )
            # Cierre forzado de lo que quede abierto al agotarse los datos.
            for position in list(stack.positions.open_positions):
                await engine.close_position(position, ExitReason.MANUAL)

        trades = list(stack.journal.all())
        statistics = StatisticsEngine(
            config.initial_balance, risk_free_rate=self._settings.risk_free_rate
        ).compute(trades, equity_curve)
        final = stack.portfolio.snapshot(stack.positions.open_positions)
        enriched = BacktestConfig(
            symbol=config.symbol,
            timeframe=config.timeframe,
            initial_balance=config.initial_balance,
            spread_bps=config.spread_bps,
            label=config.label,
            parameters=config.parameters,
            start=candles[0].start,
            end=candles[-1].end,
        )
        return BacktestResult(
            config=enriched,
            trades=trades,
            equity_curve=equity_curve,
            statistics=statistics.to_dict(),
            bars=len(candles),
            final_equity=final.equity,
            final_balance=final.balance,
        )
