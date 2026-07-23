"""QuantCore: fachada de las APIs internas del núcleo cuantitativo.

Punto único de entrada para el resto del sistema (dashboard, fases futuras):
gestión de estrategias, análisis bajo demanda, consenso, filtros y régimen.
"""

from pathlib import Path
from typing import Any

from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.decision_engine import DecisionEngine
from app.engine.evaluation import PerformanceTracker
from app.engine.feature_store import FeatureStore
from app.engine.filters import FilterChain
from app.engine.interfaces.strategy import BaseStrategy
from app.engine.market_context import MarketContextEngine
from app.engine.models import (
    ConsensusResult,
    Decision,
    FilterResult,
    MarketContext,
    RegimeState,
    StrategySignal,
)
from app.engine.plugins import PluginLoader
from app.engine.regime_detection import RegimeDetector
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import HistoryWriter, SignalHistoryStore
from app.engine.strategy_engine import StrategyEngine


class QuantCore:
    """Internal API surface of the quant brain.

    Args:
        strategies: Strategy Engine.
        signals: Signal Engine.
        decisions: Decision Engine.
        consensus: Motor de consenso.
        confidence: Confidence Engine.
        context: Market Context Engine.
        regime: Detector de régimen.
        filters: Cadena de filtros.
        features: Feature Store.
        history: Historial.
        loader: Cargador de plugins.
        writer: Persistencia del historial (opcional).
    """

    def __init__(
        self,
        *,
        strategies: StrategyEngine,
        signals: SignalEngine,
        decisions: DecisionEngine,
        consensus: ConsensusEngine,
        confidence: ConfidenceEngine,
        context: MarketContextEngine,
        regime: RegimeDetector,
        filters: FilterChain,
        features: FeatureStore,
        history: SignalHistoryStore,
        loader: PluginLoader,
        writer: HistoryWriter | None = None,
        performance: PerformanceTracker | None = None,
    ) -> None:
        self.strategies = strategies
        self.signals = signals
        self.decisions = decisions
        self.consensus = consensus
        self.confidence = confidence
        self.context = context
        self.regime = regime
        self.filters = filters
        self.features = features
        self.history = history
        self.loader = loader
        self.writer = writer
        self.performance = performance

    # ------------------------------------------------------------------
    # Gestión de estrategias
    # ------------------------------------------------------------------

    async def load_strategy(self, path: Path, class_name: str) -> None:
        """Load one strategy class from a plugin file at runtime."""
        cls: type[BaseStrategy] = self.loader.load_class(path, class_name)
        await self.strategies.load_strategy(cls)

    async def unload_strategy(self, name: str) -> None:
        """Unload a strategy."""
        await self.strategies.unload_strategy(name)

    def enable_strategy(self, name: str) -> None:
        """Enable a loaded strategy."""
        self.strategies.enable_strategy(name)

    def disable_strategy(self, name: str) -> None:
        """Disable a loaded strategy."""
        self.strategies.disable_strategy(name)

    # ------------------------------------------------------------------
    # Análisis bajo demanda
    # ------------------------------------------------------------------

    async def analyze_market(self, symbol: str) -> MarketContext:
        """Build the market context for a symbol on demand."""
        return await self.context.build(symbol)

    def detect_regime(self, symbol: str) -> RegimeState:
        """Classify the market regime for a symbol."""
        return self.regime.detect(symbol)

    def build_consensus(self, symbol: str) -> ConsensusResult:
        """Run the active consensus over the symbol's active signals."""
        return self.consensus.build(symbol, self.signals.active_signals(symbol))

    def calculate_score(self, symbol: str) -> float:
        """Global consensus score for a symbol (0 sin señales)."""
        return self.build_consensus(symbol).score

    async def calculate_confidence(self, symbol: str) -> tuple[float, dict[str, float]]:
        """Confidence (con desglose) para las señales activas del símbolo."""
        active = self.signals.active_signals(symbol)
        context = await self.context.build(symbol)
        consensus = self.consensus.build(symbol, active, context)
        return self.confidence.compute(active, consensus, context)

    async def evaluate_filters(self, symbol: str) -> list[FilterResult]:
        """Run the filter chain for a symbol's current state."""
        context = await self.context.build(symbol)
        consensus = self.consensus.build(symbol, self.signals.active_signals(symbol), context)
        return self.filters.evaluate(context, consensus)

    async def generate_signal(self, signal: StrategySignal) -> bool:
        """Inject a signal manually (testing/diagnóstico).

        Returns:
            ``True`` si la señal quedó activa.
        """
        return await self.signals.submit(signal)

    async def decide(self, symbol: str) -> Decision:
        """Force a decision evaluation for a symbol right now."""
        return await self.decisions.evaluate(symbol)

    # ------------------------------------------------------------------
    # Diagnóstico
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Full diagnostic snapshot (dashboard)."""
        return {
            "strategies": self.strategies.status(),
            "signals": self.signals.status(),
            "consensus": self.consensus.status(),
            "filters": {"enabled": self.filters.names},
            "history": self.history.status(),
            "writer": self.writer.status() if self.writer else None,
            "performance": self.performance.status() if self.performance else None,
        }
