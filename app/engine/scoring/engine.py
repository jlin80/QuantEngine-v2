"""Score Engine: normalización y agregación de puntuaciones 0-100."""

from collections.abc import Mapping, Sequence

from app.engine.models import Direction, StrategySignal


def clamp_score(value: float) -> float:
    """Clamp any value into the 0-100 scale."""
    return max(0.0, min(100.0, value))


class ScoreEngine:
    """Aggregates per-strategy scores into a global score."""

    @staticmethod
    def normalize(signal: StrategySignal) -> float:
        """Score de una señal, garantizado en 0-100."""
        return clamp_score(signal.score)

    @staticmethod
    def weighted_global(
        signals: Sequence[StrategySignal], weights: Mapping[str, float], *, default: float = 1.0
    ) -> float:
        """Weighted mean of every signal score (dirección ignorada).

        Args:
            signals: Señales a agregar.
            weights: Peso por estrategia.
            default: Peso para estrategias no configuradas.

        Returns:
            Score global 0-100 (0 si no hay señales).
        """
        total_weight = 0.0
        acc = 0.0
        for signal in signals:
            weight = weights.get(signal.strategy_name, default)
            acc += clamp_score(signal.score) * weight
            total_weight += weight
        return acc / total_weight if total_weight > 0 else 0.0

    @staticmethod
    def signed_global(
        signals: Sequence[StrategySignal], weights: Mapping[str, float], *, default: float = 1.0
    ) -> float:
        """Weighted signed score: long suma, short resta, neutral no vota.

        Returns:
            Valor en [-100, 100]; el signo es la dirección dominante.
        """
        total_weight = 0.0
        acc = 0.0
        for signal in signals:
            if signal.direction is Direction.NEUTRAL:
                continue
            weight = weights.get(signal.strategy_name, default)
            sign = 1.0 if signal.direction is Direction.LONG else -1.0
            acc += sign * clamp_score(signal.score) * weight
            total_weight += weight
        return acc / total_weight if total_weight > 0 else 0.0
