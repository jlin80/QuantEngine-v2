"""Fuente de decisiones del backtest respaldada por el QuantCore real.

Conecta la biblioteca de estrategias real (las mismas que corren en vivo) al
motor de backtest: reproduce, **de forma síncrona y vela por vela**, el mismo
pipeline que en producción es asíncrono y orientado a eventos —
``estrategias → señales → consenso → confianza → filtros → decisión``.

Contrato anti-lookahead: en la vela ``index`` sólo se alimentan al estado de
mercado las velas ``<= index``; ninguna estrategia puede ver el futuro.

No usa base de datos, scheduler ni Event Bus (todos opcionales y aquí en
``None``): sólo la lógica de decisión pura, para que el backtest sea barato y
determinista. La salida es el mismo :class:`DecisionGenerated` que consume el
Execution Engine, de modo que sizing, stops, spread y riesgo del backtest son
idénticos a los de producción.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.backtesting.htf import HigherTimeframeAggregator
from app.cache.memory import InMemoryCache
from app.cache.service import CacheService
from app.config.settings import Settings
from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.decision_engine import DecisionEngine
from app.engine.events import DecisionGenerated
from app.engine.feature_store import FeatureStore
from app.engine.filters import build_filter_chain
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.market_context import MarketContextEngine
from app.engine.models import CadenceKind, SignalRecord
from app.engine.plugins import PluginLoader
from app.engine.regime_detection import RegimeDetector
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import SignalHistoryStore
from app.engine.validators import SignalValidator
from app.market.cache import MarketCache
from app.market.models import Candle, Ticker, Timeframe
from app.market.services import MarketDataService, MarketStateStore
from app.utils.time import utc_now

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.engine.interfaces.strategy import BaseStrategy

_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_LOCK = threading.Lock()


def _shared_loop() -> asyncio.AbstractEventLoop:
    """Lazy process-wide background event loop for running the async pipeline.

    Compartido entre todas las instancias del source para que una optimización
    (decenas de backtests) no cree un hilo por corrida.
    """
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name="quant-backtest-loop", daemon=True
            ).start()
            _LOOP = loop
        return _LOOP


def make_quant_source_factory(
    base_settings: Settings, symbol: str, *, spread_bps: float
) -> Callable[[dict[str, object]], QuantCoreDecisionSource]:
    """Build a factory ``params -> QuantCoreDecisionSource`` for optimization.

    Cada juego de parámetros construye una copia independiente de settings con
    los umbrales de entrada sobreescritos (``min_score``/``min_confidence``/
    ``min_agreement``), de modo que el walk-forward optimice la selectividad de
    entrada sin que los trials se pisen. Los parámetros desconocidos se ignoran.

    Args:
        base_settings: Configuración base a clonar por trial.
        symbol: Símbolo a simular.
        spread_bps: Spread real del broker.

    Returns:
        Fábrica que produce un source configurado para un dict de parámetros.
    """

    def factory(params: dict[str, object]) -> QuantCoreDecisionSource:
        trial = base_settings.model_copy(deep=True)
        consensus = trial.quant.consensus
        if "min_score" in params:
            consensus.min_score = float(params["min_score"])  # type: ignore[arg-type]
        if "min_confidence" in params:
            consensus.min_confidence = float(params["min_confidence"])  # type: ignore[arg-type]
        if "min_agreement" in params:
            consensus.min_agreement = float(params["min_agreement"])  # type: ignore[arg-type]
        trial.quant.enabled = True
        return QuantCoreDecisionSource(trial, symbol, spread_bps=spread_bps)

    return factory


def run_quantcore_backtest(
    settings: Settings,
    symbol: str,
    candles: Sequence[Candle],
    *,
    spread_bps: float,
    balance: float = 1000.0,
) -> dict[str, float | int | str]:
    """Run the real-QuantCore backtest at real spread and at zero spread.

    El backtest con spread real refleja lo que operaría el bot; el de spread 0
    aísla si el edge (o su falta) viene de las estrategias o del costo. Devuelve
    un dict plano listo para el ``MetricsGrid`` del dashboard.

    Args:
        settings: Configuración central (usa ``quant``/``backtesting``).
        symbol: Símbolo a simular.
        candles: Serie histórica 1m.
        spread_bps: Spread real del broker.
        balance: Balance inicial.

    Returns:
        Métricas de ambos escenarios más metadatos de la corrida.
    """
    # Import diferido: evita un ciclo api → quant_source → api en carga.
    from app.backtesting.api import BacktestLab

    lab = BacktestLab(settings)

    def _one(spread: float) -> dict[str, float | int]:
        source = QuantCoreDecisionSource(settings, symbol, spread_bps=spread)
        try:
            config = lab.make_config(
                symbol,
                timeframe="1m",
                label=f"{symbol.lower()}-quantcore-{spread:g}bps",
                spread_bps=spread,
                initial_balance=balance,
            )
            result = lab.run_backtest(candles, source, config)
        finally:
            source.close()
        st = result.statistics
        return {
            "trades": int(st.get("total_trades", 0) or 0),
            "win_rate_pct": round((st.get("win_rate", 0.0) or 0.0) * 100.0, 1),
            "profit_factor": round(float(st.get("profit_factor", 0.0) or 0.0), 3),
            "expectancy_r": round(float(st.get("expectancy_r", 0.0) or 0.0), 3),
            "return_pct": round(result.return_pct, 3),
            "max_drawdown_pct": round(float(st.get("max_drawdown_pct", 0.0) or 0.0), 3),
        }

    real = _one(spread_bps)
    zero = _one(0.0)
    return {
        "symbol": symbol.upper(),
        "bars": len(candles),
        "spread_bps": spread_bps,
        "trades": real["trades"],
        "win_rate_pct": real["win_rate_pct"],
        "profit_factor": real["profit_factor"],
        "expectancy_r": real["expectancy_r"],
        "return_pct": real["return_pct"],
        "max_drawdown_pct": real["max_drawdown_pct"],
        "zero_spread_trades": zero["trades"],
        "zero_spread_win_rate_pct": zero["win_rate_pct"],
        "zero_spread_profit_factor": zero["profit_factor"],
        "zero_spread_return_pct": zero["return_pct"],
    }


class QuantCoreDecisionSource:
    """DecisionSource que ejecuta el QuantCore real sobre datos históricos.

    Args:
        settings: Configuración central (usa ``quant``); las mismas estrategias,
            umbrales y filtros que en vivo.
        symbol: Símbolo a simular.
        spread_bps: Spread a materializar en el ticker sintético de cada vela
            (debe reflejar el spread real del broker, p. ej. ~5.3 bps en ETH de
            Exness) para que los filtros y el contexto vean el costo real.
        higher_timeframes: Marcos superiores a construir desde la serie de 1m y
            publicar en el estado de mercado. El motor es hoy monotimeframe (todo
            1m, 50 minutos de visión); esto permite **medir** si un contexto
            superior aporta algo antes de cablearlo. Sólo se publican velas ya
            cerradas: ver :mod:`app.backtesting.htf`.
        signal_sink: Sumidero de cada señal admitida, igual que el de producción
            (``bootstrap._signal_sink``). Existe para poder enganchar el
            **evaluador continuo** al backtest y medir la calidad de la señal —
            todas las señales, no sólo las que llegaron a operación.
        on_bar_end: Se invoca al terminar cada vela, con esa vela. Es el punto
            donde el evaluador continuo resuelve sus operaciones virtuales, para
            que sólo vea precio ya publicado y no el futuro.
    """

    def __init__(
        self,
        settings: Settings,
        symbol: str,
        *,
        spread_bps: float,
        higher_timeframes: Sequence[Timeframe] = (),
        signal_sink: Callable[[SignalRecord], None] | None = None,
        on_bar_end: Callable[[Candle], None] | None = None,
    ) -> None:
        self._settings = settings
        self._symbol = symbol.upper()
        self._spread_frac = spread_bps / 10_000.0
        self._state = MarketStateStore()
        cache = MarketCache(CacheService(primary=None, fallback=InMemoryCache()))
        self._market = MarketDataService(self._state, cache, None)
        self._features = FeatureStore(self._market)
        self._higher_timeframes = tuple(higher_timeframes)
        self._aggregators = [
            HigherTimeframeAggregator(timeframe=tf, symbol=self._symbol)
            for tf in self._higher_timeframes
        ]

        quant = settings.quant
        self._regime = RegimeDetector(self._market, quant.regime)
        self._context_engine = MarketContextEngine(
            self._market, self._features, self._regime, quant.context
        )
        self._on_bar_end = on_bar_end
        self._history = SignalHistoryStore(
            memory_limit=quant.history.memory_limit, signal_sink=signal_sink
        )
        weights = {name: cfg.weight for name, cfg in quant.strategies.items()}
        consensus = ConsensusEngine(
            quant.consensus, weights, performance_factor=self._history.performance_factor
        )
        confidence = ConfidenceEngine(quant.confidence)
        filters = build_filter_chain(
            quant.filters,
            drawdown_reader=lambda: self._history.get_state("daily_drawdown_pct"),
            recent_decisions=lambda: self._history.decisions(limit=50),
        )
        self._signals = SignalEngine(
            SignalValidator(),
            self._history,
            None,
            signal_ttl_seconds=quant.signal_ttl_seconds,
            dedupe_window_seconds=quant.dedupe_window_seconds,
        )
        self._decisions = DecisionEngine(
            self._signals,
            self._context_engine,
            consensus,
            confidence,
            filters,
            self._history,
            quant.consensus,
            None,
            None,
        )
        self._loader = PluginLoader(list(quant.plugin_dirs))
        self._strategies: list[BaseStrategy] = []
        self._fed_index = -1
        self._initialized = False
        # El motor de backtest ya corre dentro de un event loop; para ejecutar
        # el pipeline async sin colisionar, se usa un loop compartido en un hilo
        # de fondo. Compartido (no uno por instancia) para que una optimización
        # con decenas de corridas no acumule hilos.
        self._loop = _shared_loop()

    def close(self) -> None:
        """Kept for API compatibility (the shared loop lives for the process)."""
        return None

    @property
    def market(self) -> MarketDataService:
        """El mercado que ve la estrategia: sólo velas ``<= índice actual``.

        Se expone para que el evaluador continuo pueda resolver sus operaciones
        virtuales contra **exactamente** los mismos datos, sin abrirle una vía
        al futuro. Cambia de instancia en cada :meth:`reset`, así que hay que
        leerlo en cada uso y no cachearlo.
        """
        return self._market

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _ensure_initialized(self) -> None:
        """Load and initialize the real strategy library once."""
        if self._initialized:
            return
        for cls in self._loader.discover():
            config = self._settings.quant.strategy_settings(cls.name)
            if not config.enabled:
                continue
            # Sólo estrategias de vela 1m con el símbolo permitido (o todos).
            if cls.timeframe is not Timeframe.M1:
                continue
            if cls.symbols and self._symbol not in {s.upper() for s in cls.symbols}:
                continue
            strategy = cls(dict(config.parameters))
            await strategy.initialize(self._features)
            self._strategies.append(strategy)
        self._initialized = True

    def reset(self) -> None:
        """Forget fed candles and signal state before a fresh run."""
        self._fed_index = -1
        self._state = MarketStateStore()
        cache = MarketCache(CacheService(primary=None, fallback=InMemoryCache()))
        self._market = MarketDataService(self._state, cache, None)
        self._features = FeatureStore(self._market)
        for aggregator in self._aggregators:
            aggregator.reset()

    # ------------------------------------------------------------------
    # DecisionSource
    # ------------------------------------------------------------------

    def decide(
        self, symbol: str, candles: Sequence[Candle], index: int
    ) -> DecisionGenerated | None:
        """Run the real quant pipeline for bar ``index`` (sync wrapper).

        Ejecuta la corrutina en el loop del hilo dedicado y espera el resultado,
        para funcionar tanto fuera como dentro de un event loop en curso.
        """
        future = asyncio.run_coroutine_threadsafe(
            self._decide_async(symbol.upper(), candles, index), self._loop
        )
        return future.result()

    async def _decide_async(
        self, symbol: str, candles: Sequence[Candle], index: int
    ) -> DecisionGenerated | None:
        await self._ensure_initialized()
        self._feed_up_to(candles, index)
        self._features.invalidate(symbol)

        try:
            return await self._decide_inner(symbol, candles, index)
        finally:
            if self._on_bar_end is not None:
                self._on_bar_end(candles[index])

    async def _decide_inner(
        self, symbol: str, candles: Sequence[Candle], index: int
    ) -> DecisionGenerated | None:
        """Evaluate the strategy library for the current bar.

        Las 20 estrategias se consultan **antes** de evaluar el consenso, igual
        que en producción: allí el Signal Engine acumula las señales de todas y
        el Decision Engine decide sobre el conjunto. Evaluar dentro del bucle
        —una vez por señal admitida, cortando en la primera aceptada— hacía que
        decidiera quien llegara primero en el orden de carga de plugins, con un
        subconjunto del consenso.
        """
        # Un solo contexto por vela: todas las estrategias miran el mismo
        # mercado en el mismo instante, así que reconstruirlo por estrategia
        # sólo costaba tiempo.
        context = await self._context_engine.build(symbol)
        fired_at = utc_now()
        submitted = False
        for strategy in self._strategies:
            if strategy.symbols and symbol not in {s.upper() for s in strategy.symbols}:
                continue
            if strategy.cadence.kind is not CadenceKind.EVERY_CANDLE:
                continue
            ctx = AnalysisContext(
                symbol=symbol,
                fired_at=fired_at,
                trigger="candle:1m",
                market=self._market,
                features=self._features,
                context=context,
            )
            signal = await strategy.analyze(ctx)
            if signal is None:
                continue
            submitted = await self._signals.submit(signal) or submitted
        if not submitted:
            return None
        decision = await self._decisions.evaluate(symbol)
        if not decision.accepted or decision.action.value not in ("open_long", "open_short"):
            return None
        return DecisionGenerated(
            source="backtest-quantcore",
            decision_id=decision.decision_id,
            symbol=symbol,
            action=decision.action.value,
            accepted=True,
            score=decision.score,
            confidence=decision.confidence,
            summary=decision.explanation[0] if decision.explanation else "",
            # Sin atribución la ejecución no puede aplicar el toggle por
            # estrategia (`execution.strategies_enabled`) ni el holding mínimo
            # por estrategia del Bloque 1: ambos comprueban `decision.strategy`
            # y con "" no bloquean ni resuelven nada. Es la vía por la que el
            # laboratorio operaba estrategias que producción tiene apagadas.
            strategy=decision.primary_strategy,
            strategy_category=decision.primary_category,
            signal_ids=decision.signals_considered,
        )

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _feed_up_to(self, candles: Sequence[Candle], index: int) -> None:
        """Push candles ``(_fed_index, index]`` into the market state.

        Sólo hacia adelante: nunca alimenta velas futuras (> index).
        """
        for i in range(self._fed_index + 1, index + 1):
            self._state.update_candle(candles[i])
            # Marcos superiores: cada agregador devuelve una vela sólo cuando
            # queda CERRADA. Publicar la que está en curso daría a la estrategia
            # el máximo y el mínimo de minutos que aún no han ocurrido.
            for aggregator in self._aggregators:
                higher = aggregator.push(candles[i])
                if higher is not None:
                    self._state.update_candle(higher)
        self._fed_index = index
        last = candles[index]
        half = last.close * self._spread_frac / 2.0
        now = utc_now()
        self._state.update_ticker(
            Ticker(
                symbol=self._symbol,
                provider="backtest",
                bid=last.close - half,
                ask=last.close + half,
                exchange_ts=now,
                local_ts=now,
            )
        )
