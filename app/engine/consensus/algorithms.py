"""Algoritmos de consenso intercambiables.

Todos operan sobre las señales activas de un símbolo y un mapa de pesos por
estrategia. Ninguna estrategia decide sola: el consenso agrega.
"""

from collections.abc import Callable, Mapping, Sequence

from app.engine.interfaces.consensus import ConsensusAlgorithm
from app.engine.models import (
    ConsensusResult,
    Direction,
    MarketContext,
    StrategySignal,
)
from app.engine.scoring import clamp_score


def _empty(symbol: str, method: str, reason: str) -> ConsensusResult:
    """Consensus with no actionable signals."""
    return ConsensusResult(
        method=method,
        symbol=symbol,
        direction=Direction.NEUTRAL,
        score=0.0,
        agreement=0.0,
        reasons=(reason,),
    )


def _actionable(signals: Sequence[StrategySignal]) -> list[StrategySignal]:
    """Signals that vote (neutral no vota)."""
    return [s for s in signals if s.direction is not Direction.NEUTRAL]


class MajorityVoting(ConsensusAlgorithm):
    """Cada estrategia vota 1; gana la dirección con más votos."""

    name = "majority_voting"

    def build(
        self,
        symbol: str,
        signals: Sequence[StrategySignal],
        weights: Mapping[str, float],
        context: MarketContext | None = None,
    ) -> ConsensusResult:
        """Aggregate by simple vote count."""
        voters = _actionable(signals)
        if not voters:
            return _empty(symbol, self.name, "sin señales direccionales")
        longs = [s for s in voters if s.direction is Direction.LONG]
        shorts = [s for s in voters if s.direction is Direction.SHORT]
        if len(longs) == len(shorts):
            return ConsensusResult(
                method=self.name,
                symbol=symbol,
                direction=Direction.NEUTRAL,
                score=0.0,
                agreement=0.5,
                participants=tuple(s.strategy_name for s in voters),
                reasons=("empate de votos long/short",),
            )
        winners = longs if len(longs) > len(shorts) else shorts
        direction = Direction.LONG if winners is longs else Direction.SHORT
        score = sum(clamp_score(s.score) for s in winners) / len(winners)
        return ConsensusResult(
            method=self.name,
            symbol=symbol,
            direction=direction,
            score=round(score, 2),
            agreement=round(len(winners) / len(voters), 4),
            participants=tuple(s.strategy_name for s in voters),
            contributions={s.strategy_name: clamp_score(s.score) for s in winners},
            reasons=(f"{len(winners)}/{len(voters)} estrategias votan {direction.value}",),
        )


class WeightedVoting(ConsensusAlgorithm):
    """Votos ponderados por el peso configurado de cada estrategia."""

    name = "weighted_voting"

    def __init__(self, *, default_weight: float = 1.0) -> None:
        self._default = default_weight

    def build(
        self,
        symbol: str,
        signals: Sequence[StrategySignal],
        weights: Mapping[str, float],
        context: MarketContext | None = None,
    ) -> ConsensusResult:
        """Aggregate by weighted vote mass per direction."""
        voters = _actionable(signals)
        if not voters:
            return _empty(symbol, self.name, "sin señales direccionales")
        mass = {Direction.LONG: 0.0, Direction.SHORT: 0.0}
        for signal in voters:
            mass[signal.direction] += weights.get(signal.strategy_name, self._default)
        total = mass[Direction.LONG] + mass[Direction.SHORT]
        if total <= 0 or mass[Direction.LONG] == mass[Direction.SHORT]:
            return _empty(symbol, self.name, "masa de votos empatada o nula")
        direction = (
            Direction.LONG if mass[Direction.LONG] > mass[Direction.SHORT] else Direction.SHORT
        )
        winners = [s for s in voters if s.direction is direction]
        weight_sum = sum(weights.get(s.strategy_name, self._default) for s in winners)
        score = (
            sum(clamp_score(s.score) * weights.get(s.strategy_name, self._default) for s in winners)
            / weight_sum
            if weight_sum > 0
            else 0.0
        )
        return ConsensusResult(
            method=self.name,
            symbol=symbol,
            direction=direction,
            score=round(score, 2),
            agreement=round(mass[direction] / total, 4),
            participants=tuple(s.strategy_name for s in voters),
            contributions={
                s.strategy_name: round(
                    clamp_score(s.score) * weights.get(s.strategy_name, self._default), 2
                )
                for s in winners
            },
            reasons=(f"peso {mass[direction]:.2f}/{total:.2f} a favor de {direction.value}",),
        )


class WeightedAverage(ConsensusAlgorithm):
    """Promedio ponderado con signo: long suma, short resta."""

    name = "weighted_average"

    def __init__(self, *, default_weight: float = 1.0) -> None:
        self._default = default_weight

    def _weights_for(
        self, signals: Sequence[StrategySignal], weights: Mapping[str, float]
    ) -> dict[str, float]:
        """Effective weight per strategy (hook para subclases)."""
        return {s.strategy_name: weights.get(s.strategy_name, self._default) for s in signals}

    def build(
        self,
        symbol: str,
        signals: Sequence[StrategySignal],
        weights: Mapping[str, float],
        context: MarketContext | None = None,
    ) -> ConsensusResult:
        """Aggregate by signed weighted average of scores."""
        voters = _actionable(signals)
        if not voters:
            return _empty(symbol, self.name, "sin señales direccionales")
        effective = self._effective_weights(voters, weights, context)
        total_weight = sum(effective.get(s.strategy_name, self._default) for s in voters)
        if total_weight <= 0:
            return _empty(symbol, self.name, "peso total nulo")
        signed = 0.0
        contributions: dict[str, float] = {}
        winning_mass = {Direction.LONG: 0.0, Direction.SHORT: 0.0}
        for signal in voters:
            weight = effective.get(signal.strategy_name, self._default)
            sign = 1.0 if signal.direction is Direction.LONG else -1.0
            contribution = sign * clamp_score(signal.score) * weight
            signed += contribution
            contributions[signal.strategy_name] = round(contribution / total_weight, 2)
            winning_mass[signal.direction] += weight
        value = signed / total_weight
        direction = (
            Direction.NEUTRAL
            if abs(value) < 1e-9
            else Direction.LONG if value > 0 else Direction.SHORT
        )
        agreement = (
            winning_mass[direction] / total_weight if direction is not Direction.NEUTRAL else 0.5
        )
        return ConsensusResult(
            method=self.name,
            symbol=symbol,
            direction=direction,
            score=round(abs(value), 2),
            agreement=round(agreement, 4),
            participants=tuple(s.strategy_name for s in voters),
            contributions=contributions,
            reasons=(f"promedio ponderado con signo = {value:+.2f}",),
        )

    def _effective_weights(
        self,
        signals: Sequence[StrategySignal],
        weights: Mapping[str, float],
        context: MarketContext | None,
    ) -> dict[str, float]:
        """Weights actually used (subclases los modulan)."""
        return self._weights_for(signals, weights)


class DynamicWeighting(WeightedAverage):
    """Promedio ponderado modulado por el rendimiento reciente por estrategia.

    El factor de rendimiento (0-1, 0.5 = neutro) lo alimenta el StateManager
    a partir del historial de señales; sin historial, equivale al promedio
    ponderado clásico.
    """

    name = "dynamic_weighting"

    def __init__(
        self,
        performance_factor: Callable[[str], float] | None = None,
        *,
        default_weight: float = 1.0,
    ) -> None:
        super().__init__(default_weight=default_weight)
        self._performance = performance_factor or (lambda _name: 0.5)

    def _effective_weights(
        self,
        signals: Sequence[StrategySignal],
        weights: Mapping[str, float],
        context: MarketContext | None,
    ) -> dict[str, float]:
        """Base weight x (0.5 + performance) — rendimiento 0.5 = sin cambio."""
        base = self._weights_for(signals, weights)
        return {name: weight * (0.5 + self._performance(name)) for name, weight in base.items()}


class RegimeWeighting(WeightedAverage):
    """Promedio ponderado modulado por el régimen de mercado.

    Los multiplicadores vienen de configuración:
    ``{"trending": {"momentum": 1.5, "meanrev": 0.5}, ...}``.
    """

    name = "regime_weighting"

    def __init__(
        self,
        regime_multipliers: Mapping[str, Mapping[str, float]] | None = None,
        *,
        default_weight: float = 1.0,
    ) -> None:
        super().__init__(default_weight=default_weight)
        self._multipliers = regime_multipliers or {}

    def _effective_weights(
        self,
        signals: Sequence[StrategySignal],
        weights: Mapping[str, float],
        context: MarketContext | None,
    ) -> dict[str, float]:
        """Base weight x multiplicador del régimen activo."""
        base = self._weights_for(signals, weights)
        if context is None or context.regime is None:
            return base
        table = self._multipliers.get(context.regime.primary.value, {})
        return {name: weight * table.get(name, 1.0) for name, weight in base.items()}
