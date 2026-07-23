"""Aprendizaje por refuerzo para ponderación adaptativa (Fase 7).

Implementa un bandit epsilon-greedy funcional sobre "brazos" (estrategias o
modelos): cada resultado actualiza el valor estimado del brazo y de ahí salen
pesos adaptativos. El refuerzo profundo y el aprendizaje online (policy
gradient, actualización incremental por muestra) quedan como **estructura
preparada**: se declaran pero fallan con un mensaje claro hasta una fase futura,
igual que los optimizadores avanzados del laboratorio.
"""

import math
import random
from typing import Any

from app.core.exceptions import MLError
from app.ml.models.math import normalize


class EpsilonGreedyBandit:
    """Epsilon-greedy multi-armed bandit for adaptive weighting.

    Args:
        arms: Nombres de los brazos (estrategias/modelos).
        epsilon: Probabilidad de exploración [0, 1].
        seed: Semilla para el determinismo.
    """

    def __init__(self, arms: list[str], *, epsilon: float = 0.1, seed: int = 7) -> None:
        if not arms:
            raise MLError("Un bandit necesita al menos un brazo")
        self._epsilon = epsilon
        self._rng = random.Random(seed)
        self._values: dict[str, float] = dict.fromkeys(arms, 0.0)
        self._counts: dict[str, int] = dict.fromkeys(arms, 0)

    def update(self, arm: str, reward: float) -> None:
        """Update an arm's value estimate with a realised reward (e.g. R)."""
        if arm not in self._values:
            self._values[arm] = 0.0
            self._counts[arm] = 0
        self._counts[arm] += 1
        step = 1.0 / self._counts[arm]
        self._values[arm] += step * (reward - self._values[arm])

    def select(self) -> str:
        """Select an arm (explore with probability epsilon, else exploit)."""
        if self._rng.random() < self._epsilon:
            return self._rng.choice(list(self._values))
        return max(self._values, key=lambda arm: self._values[arm])

    def weights(self, *, temperature: float = 1.0) -> dict[str, float]:
        """Softmax weights over the current value estimates (sum to 1)."""
        arms = list(self._values)
        exps = [math.exp(self._values[a] / max(temperature, 1e-6)) for a in arms]
        normalised = normalize(exps)
        return dict(zip(arms, normalised, strict=False))

    def status(self) -> dict[str, Any]:
        """Current value estimates and pull counts."""
        return {
            "epsilon": self._epsilon,
            "values": {a: round(v, 4) for a, v in self._values.items()},
            "counts": dict(self._counts),
        }


def online_update_not_enabled() -> None:
    """Placeholder for incremental online learning (prepared, not enabled).

    Raises:
        MLError: Siempre; el aprendizaje online por muestra llega en una fase
            futura (aquí sólo queda declarada la estructura).
    """
    raise MLError(
        "El aprendizaje online incremental está preparado pero deshabilitado; "
        "el reentrenamiento es por lotes (nocturno/bajo demanda) en esta fase."
    )
