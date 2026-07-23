"""Confidence Engine: la confianza es independiente del score.

Un score alto con confianza baja NO opera. La confianza pondera calidad de
datos, volatilidad, liquidez, confirmaciones, número de estrategias
coincidentes e historial reciente — y devuelve su desglose para que toda
decisión sea explicable.
"""

from collections.abc import Sequence

from app.config.settings import QuantConfidenceSettings
from app.engine.models import (
    ConsensusResult,
    MarketContext,
    StrategySignal,
    VolatilityState,
)


class ConfidenceEngine:
    """Computes decision confidence (0-1) with a full breakdown.

    Args:
        settings: Pesos de cada factor (se normalizan entre sí).
    """

    def __init__(self, settings: QuantConfidenceSettings) -> None:
        self._settings = settings
        # Factor de historial por estrategia (lo alimenta el StateManager).
        self._history_factor: dict[str, float] = {}

    def set_history_factor(self, strategy: str, factor: float) -> None:
        """Update the recent-performance factor for a strategy (0-1)."""
        self._history_factor[strategy] = max(0.0, min(1.0, factor))

    def compute(
        self,
        signals: Sequence[StrategySignal],
        consensus: ConsensusResult,
        context: MarketContext,
    ) -> tuple[float, dict[str, float]]:
        """Compute the confidence for a prospective decision.

        Args:
            signals: Señales consideradas.
            consensus: Consenso alcanzado.
            context: Contexto de mercado.

        Returns:
            ``(confianza 0-1, desglose por factor)``.
        """
        factors: dict[str, float] = {
            "signal_confidence": self._signal_confidence(signals),
            "agreement": max(0.0, min(1.0, consensus.agreement)),
            "data_quality": context.data_quality,
            "liquidity": self._liquidity(context),
            "volatility": self._volatility(context),
            "confirmation": self._confirmation(signals),
            "history": self._history(signals),
        }
        weights = {
            "signal_confidence": self._settings.signal_confidence,
            "agreement": self._settings.agreement,
            "data_quality": self._settings.data_quality,
            "liquidity": self._settings.liquidity,
            "volatility": self._settings.volatility,
            "confirmation": self._settings.confirmation,
            "history": self._settings.history,
        }
        total = sum(weights.values())
        if total <= 0:
            return 0.0, factors
        confidence = sum(factors[name] * weight for name, weight in weights.items()) / total
        return round(max(0.0, min(1.0, confidence)), 4), {
            name: round(value, 4) for name, value in factors.items()
        }

    @staticmethod
    def _signal_confidence(signals: Sequence[StrategySignal]) -> float:
        """Mean self-reported confidence of the signals."""
        if not signals:
            return 0.0
        return sum(s.confidence for s in signals) / len(signals)

    @staticmethod
    def _liquidity(context: MarketContext) -> float:
        """1.0 con spread sano y volumen suficiente; penaliza cada carencia."""
        value = 1.0
        if context.spread_elevated:
            value -= 0.5
        if not context.volume_sufficient:
            value -= 0.5
        return max(0.0, value)

    @staticmethod
    def _volatility(context: MarketContext) -> float:
        """Volatilidad normal = 1.0; extremos reducen la confianza."""
        if context.volatility is VolatilityState.HIGH:
            return 0.5
        if context.volatility is VolatilityState.LOW:
            return 0.6
        return 1.0

    @staticmethod
    def _confirmation(signals: Sequence[StrategySignal]) -> float:
        """Señales que exigen confirmación sin acompañante la reducen."""
        if not signals:
            return 0.0
        if len(signals) > 1:
            return 1.0  # hay acompañantes: las confirmaciones existen
        return 0.5 if signals[0].required_confirmation else 1.0

    def _history(self, signals: Sequence[StrategySignal]) -> float:
        """Recent-performance factor (0.5 neutro sin historial)."""
        if not signals:
            return 0.5
        values = [self._history_factor.get(s.strategy_name, 0.5) for s in signals]
        return sum(values) / len(values)
