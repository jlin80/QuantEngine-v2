"""Strategy Engine: ejecuta decenas de estrategias sin que se bloqueen.

Cada estrategia corre con su propia cadencia (tick, segundo, vela cerrada,
intervalo), aislada: su excepción no toca a las demás, y si una evaluación
sigue corriendo cuando llega el siguiente disparo, el disparo se salta y se
contabiliza. Toda señal producida entra al Signal Engine y dispara la
evaluación del Decision Engine para ese símbolo.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from app.config.settings import QuantSettings
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.events.events import StrategyLoaded
from app.core.exceptions import ConfigurationError, EventBusError
from app.core.lifecycle import Service
from app.engine.decision_engine import DecisionEngine
from app.engine.events import (
    StrategyExecuted,
    StrategyFailed,
    StrategyScoreUpdated,
    StrategyUnloaded,
)
from app.engine.feature_store import FeatureStore
from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.market_context import MarketContextEngine
from app.engine.models import CadenceKind, StrategyStats
from app.engine.plugins import PluginLoader
from app.engine.signal_engine import SignalEngine
from app.market.events import CandleClosed, NewTick
from app.market.services import MarketDataService
from app.scheduler.scheduler import AsyncScheduler
from app.utils.time import utc_now


class _LoadedStrategy:
    """Runtime wrapper of a strategy instance."""

    def __init__(self, strategy: BaseStrategy, *, enabled: bool, weight: float) -> None:
        self.strategy = strategy
        self.enabled = enabled
        self.weight = weight
        self.lock = asyncio.Lock()
        self.stats = StrategyStats(
            name=strategy.name,
            version=strategy.version,
            enabled=enabled,
            symbols=tuple(strategy.symbols),
            cadence=strategy.cadence.describe(),
            weight=weight,
        )


class StrategyEngine(Service):
    """Owns every loaded strategy and drives their execution.

    Args:
        settings: Configuración del Quant Core.
        loader: Cargador de plugins.
        market: API de datos agnóstica del broker.
        features: Feature Store compartido.
        context_engine: Market Context Engine.
        signal_engine: Registro de señales.
        decision_engine: Decision Engine (se invoca tras cada señal).
        bus: Event Bus.
        scheduler: Scheduler interno (cadencias por tiempo).
        overrides: Proveedor de las decisiones del operador tomadas desde el
            dashboard (``nombre -> {enabled?, weight?}``), aplicadas tras el
            descubrimiento. Sin esto, apagar una estrategia sólo duraba hasta el
            siguiente reinicio, que la volvía a levantar con el valor del `.env`
            sin avisar. Se inyecta como callable para no acoplar el núcleo del
            motor al almacén del dashboard; ``None`` deja el comportamiento
            previo intacto.
    """

    def __init__(
        self,
        settings: QuantSettings,
        loader: PluginLoader,
        market: MarketDataService,
        features: FeatureStore,
        context_engine: MarketContextEngine,
        signal_engine: SignalEngine,
        decision_engine: DecisionEngine,
        bus: EventBus,
        scheduler: AsyncScheduler,
        overrides: Callable[[], dict[str, dict[str, Any]]] | None = None,
    ) -> None:
        super().__init__("strategy_engine")
        self._settings = settings
        self._loader = loader
        self._market = market
        self._features = features
        self._context_engine = context_engine
        self._signal_engine = signal_engine
        self._decision_engine = decision_engine
        self._bus = bus
        self._scheduler = scheduler
        self._overrides = overrides
        self._strategies: dict[str, _LoadedStrategy] = {}
        self._subscriptions: list[Any] = []
        self._timer_jobs: list[str] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._log = logging.getLogger("app.engine.strategies")

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _on_start(self) -> None:
        if self._settings.auto_discover:
            for cls in self._loader.discover():
                await self._register(cls)
        self._apply_overrides()
        self._subscriptions.append(self._bus.subscribe(self._on_tick, NewTick))
        self._subscriptions.append(self._bus.subscribe(self._on_candle, CandleClosed))

    async def _on_stop(self) -> None:
        for subscription in self._subscriptions:
            self._bus.unsubscribe(subscription)
        self._subscriptions.clear()
        for job in self._timer_jobs:
            with contextlib.suppress(Exception):
                self._scheduler.remove_job(job)
        self._timer_jobs.clear()
        for task in list(self._tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    # ------------------------------------------------------------------
    # Gestión de estrategias (APIs internas)
    # ------------------------------------------------------------------

    async def _register(self, cls: type[BaseStrategy]) -> None:
        """Instantiate, configure and wire one strategy class."""
        config = self._settings.strategy_settings(cls.name)
        try:
            strategy = cls(dict(config.parameters))
            await strategy.initialize(self._features)
        except Exception:
            self._log.exception("Strategy '%s' failed to initialize — skipped", cls.name)
            return
        loaded = _LoadedStrategy(strategy, enabled=config.enabled, weight=config.weight)
        self._strategies[strategy.name] = loaded
        if strategy.cadence.kind in (CadenceKind.EVERY_SECOND, CadenceKind.INTERVAL):
            self._schedule_timer(loaded)
        await self._publish(
            StrategyLoaded(
                source="strategy_engine",
                strategy=strategy.name,
                symbols=tuple(strategy.symbols),
            )
        )
        self._log.info(
            "Strategy '%s' v%s loaded (%s, enabled=%s)",
            strategy.name,
            strategy.version,
            strategy.cadence.describe(),
            config.enabled,
        )

    async def load_strategy(self, cls: type[BaseStrategy]) -> None:
        """Load a strategy class at runtime.

        Raises:
            ConfigurationError: Si ya existe una con el mismo nombre.
        """
        if cls.name in self._strategies:
            raise ConfigurationError(f"Strategy '{cls.name}' already loaded")
        await self._register(cls)

    async def unload_strategy(self, name: str) -> None:
        """Remove a strategy from the engine.

        Raises:
            ConfigurationError: Si no existe.
        """
        if name not in self._strategies:
            raise ConfigurationError(f"Strategy '{name}' is not loaded")
        del self._strategies[name]
        job_name = f"strategy_timer_{name}"
        if job_name in self._timer_jobs:
            with contextlib.suppress(Exception):
                self._scheduler.remove_job(job_name)
            self._timer_jobs.remove(job_name)
        await self._publish(StrategyUnloaded(source="strategy_engine", strategy=name))

    def enable_strategy(self, name: str) -> None:
        """Enable a loaded strategy.

        Raises:
            ConfigurationError: Si no existe.
        """
        self._require(name).enabled = True
        self._require(name).stats.enabled = True

    def disable_strategy(self, name: str) -> None:
        """Disable a loaded strategy (queda cargada pero no se ejecuta).

        Raises:
            ConfigurationError: Si no existe.
        """
        self._require(name).enabled = False
        self._require(name).stats.enabled = False

    def set_weight(self, name: str, weight: float) -> float:
        """Set the consensus weight of a loaded strategy.

        Es el único camino por el que el Meta Strategy Manager llega a afectar
        al consenso: gobierna **configuración**, nunca código ni órdenes. El
        valor se refleja también en ``stats`` para que el dashboard muestre el
        peso vigente y no el de arranque.

        Args:
            name: Estrategia cargada.
            weight: Peso nuevo (no negativo).

        Returns:
            El peso aplicado.

        Raises:
            ConfigurationError: Si la estrategia no está cargada.
        """
        loaded = self._require(name)
        applied = max(0.0, float(weight))
        loaded.weight = applied
        loaded.stats.weight = applied
        return applied

    def _apply_overrides(self) -> None:
        """Aplicar las decisiones del operador sobre las estrategias cargadas.

        Se llama tras el descubrimiento, no antes: una estrategia que ya no
        existe (renombrada, retirada del catálogo) no debe abortar el arranque
        ni desaparecer en silencio — se registra y se sigue. Un override roto no
        puede dejar al motor sin estrategias.
        """
        if self._overrides is None:
            return
        try:
            overrides = self._overrides()
        except Exception:  # pragma: no cover - el almacén no puede tumbar el motor
            self._log.exception("No se pudieron leer los overrides de estrategias")
            return
        for name, changes in overrides.items():
            if name not in self._strategies:
                self._log.warning("Override de estrategia ignorado: '%s' no está cargada", name)
                continue
            if "enabled" in changes:
                enabled = bool(changes["enabled"])
                if enabled:
                    self.enable_strategy(name)
                else:
                    self.disable_strategy(name)
                self._log.info("Override del operador: '%s' enabled=%s", name, enabled)
            if "weight" in changes:
                applied = self.set_weight(name, float(changes["weight"]))
                self._log.info("Override del operador: '%s' weight=%.3f", name, applied)

    def _require(self, name: str) -> _LoadedStrategy:
        """Loaded strategy or ConfigurationError."""
        loaded = self._strategies.get(name)
        if loaded is None:
            raise ConfigurationError(f"Strategy '{name}' is not loaded")
        return loaded

    @property
    def loaded(self) -> list[str]:
        """Names of loaded strategies."""
        return sorted(self._strategies)

    def stats(self) -> list[StrategyStats]:
        """Runtime stats per strategy (dashboard)."""
        return [loaded.stats for loaded in self._strategies.values()]

    def weights(self) -> dict[str, float]:
        """Configured weight per loaded strategy."""
        return {name: loaded.weight for name, loaded in self._strategies.items()}

    def explain(self, name: str) -> str:
        """Explanation of a strategy's latest evaluation.

        Raises:
            ConfigurationError: Si no existe.
        """
        return self._require(name).strategy.explain()

    # ------------------------------------------------------------------
    # Disparadores
    # ------------------------------------------------------------------

    def _schedule_timer(self, loaded: _LoadedStrategy) -> None:
        """Register the scheduler job for time-driven strategies."""
        strategy = loaded.strategy
        seconds = max(0.5, strategy.cadence.seconds)
        job_name = f"strategy_timer_{strategy.name}"

        async def timer_job() -> None:
            for symbol in self._symbols_for(strategy):
                await self._run_strategy(loaded, symbol, trigger="timer")

        self._scheduler.add_job(job_name, timer_job, interval_seconds=seconds)
        self._timer_jobs.append(job_name)

    async def _on_tick(self, event: Event) -> None:
        """Dispatch EVERY_TICK strategies for the tick's symbol."""
        if not isinstance(event, NewTick):
            return
        for loaded in self._strategies.values():
            if loaded.strategy.cadence.kind is not CadenceKind.EVERY_TICK:
                continue
            if self._matches(loaded.strategy, event.symbol):
                self._spawn(loaded, event.symbol, "tick", {"price": event.price})

    async def _on_candle(self, event: Event) -> None:
        """Dispatch EVERY_CANDLE strategies on matching timeframe closes."""
        if not isinstance(event, CandleClosed):
            return
        for loaded in self._strategies.values():
            cadence = loaded.strategy.cadence
            if cadence.kind is not CadenceKind.EVERY_CANDLE:
                continue
            expected = cadence.timeframe.value if cadence.timeframe else "1m"
            if event.timeframe != expected:
                continue
            if self._matches(loaded.strategy, event.symbol):
                self._spawn(
                    loaded,
                    event.symbol,
                    f"candle:{event.timeframe}",
                    {"open": event.open, "close": event.close, "volume": event.volume},
                )

    @staticmethod
    def _matches(strategy: BaseStrategy, symbol: str) -> bool:
        """Whether a strategy analyzes a symbol (vacío = todos)."""
        return not strategy.symbols or symbol.upper() in {s.upper() for s in strategy.symbols}

    def _symbols_for(self, strategy: BaseStrategy) -> tuple[str, ...]:
        """Symbols a timer-driven strategy should evaluate."""
        if strategy.symbols:
            return tuple(s.upper() for s in strategy.symbols)
        return tuple(self._market.symbols)

    def _spawn(
        self, loaded: _LoadedStrategy, symbol: str, trigger: str, payload: dict[str, Any]
    ) -> None:
        """Run one evaluation as a background task (no bloquea el bus)."""
        task = asyncio.create_task(
            self._run_strategy(loaded, symbol, trigger=trigger, payload=payload),
            name=f"strategy-{loaded.strategy.name}-{symbol}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------

    async def _run_strategy(
        self,
        loaded: _LoadedStrategy,
        symbol: str,
        *,
        trigger: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Execute one isolated evaluation of one strategy on one symbol."""
        if not loaded.enabled or not self.is_running:
            return
        strategy = loaded.strategy
        if loaded.lock.locked():
            loaded.stats.skipped += 1
            return
        async with loaded.lock:
            started = time.perf_counter()
            produced = False
            try:
                context = await self._context_engine.build(symbol)
                ctx = AnalysisContext(
                    symbol=symbol.upper(),
                    fired_at=utc_now(),
                    trigger=trigger,
                    market=self._market,
                    features=self._features,
                    context=context,
                    payload=payload or {},
                )
                signal = await strategy.analyze(ctx)
                duration_ms = (time.perf_counter() - started) * 1000.0
                self._record_run(loaded, duration_ms)
                # Detecciones analíticas acumuladas por la estrategia.
                for detection in ctx.detections:
                    await self._publish(detection)
                if signal is not None:
                    produced = True
                    strategy.record_evaluation(signal)
                    loaded.stats.signals_produced += 1
                    loaded.stats.last_signal_at = utc_now()
                    loaded.stats.last_score = signal.score
                    loaded.stats.last_confidence = signal.confidence
                    await self._publish(
                        StrategyScoreUpdated(
                            source="strategy_engine",
                            strategy=strategy.name,
                            symbol=signal.symbol,
                            score=signal.score,
                            confidence=signal.confidence,
                        )
                    )
                    problems = strategy.validate(signal)
                    if problems:
                        self._log.warning(
                            "Strategy '%s' emitted an invalid signal: %s",
                            strategy.name,
                            problems,
                        )
                    if await self._signal_engine.submit(signal):
                        await self._decision_engine.evaluate(signal.symbol)
                else:
                    strategy.record_evaluation(None, note=f"sin oportunidad ({trigger})")
                await self._publish(
                    StrategyExecuted(
                        source="strategy_engine",
                        strategy=strategy.name,
                        symbol=symbol.upper(),
                        duration_ms=round(duration_ms, 3),
                        produced_signal=produced,
                    )
                )
            except Exception as exc:
                loaded.stats.errors += 1
                self._log.exception("Strategy '%s' failed on %s", strategy.name, symbol)
                await self._publish(
                    StrategyFailed(
                        source="strategy_engine",
                        strategy=strategy.name,
                        symbol=symbol.upper(),
                        error=repr(exc),
                    )
                )

    def _record_run(self, loaded: _LoadedStrategy, duration_ms: float) -> None:
        """Update timing metrics after a run."""
        stats = loaded.stats
        stats.runs += 1
        stats.last_run_at = utc_now()
        stats.last_duration_ms = round(duration_ms, 3)
        if stats.avg_duration_ms is None:
            stats.avg_duration_ms = duration_ms
        else:
            stats.avg_duration_ms = round(0.9 * stats.avg_duration_ms + 0.1 * duration_ms, 3)

    async def healthcheck(self) -> bool:
        """Healthy while running."""
        return self.is_running

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "loaded": len(self._strategies),
            "enabled": sum(1 for s in self._strategies.values() if s.enabled),
            "strategies": [loaded.stats.to_dict() for loaded in self._strategies.values()],
            "feature_store": self._features.stats,
        }

    async def _publish(self, event: Event) -> None:
        """Publish tolerating a stopped/saturated bus."""
        try:
            await self._bus.publish(event)
        except EventBusError as exc:
            self._log.warning("Event publish failed: %s", exc)
