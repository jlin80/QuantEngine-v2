"""Utilidades numéricas en Python puro (sin numpy).

El motor evita dependencias pesadas a propósito (compatibilidad con CPUs sin
SSE4.2). Los datasets del ML son pequeños —historial de operaciones, cientos de
filas— así que Python puro es más que suficiente.
"""

import math
from collections.abc import Sequence


def sigmoid(z: float) -> float:
    """Numerically stable logistic function."""
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product of two equal-length vectors."""
    return math.fsum(x * y for x, y in zip(a, b, strict=False))


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean (0.0 for an empty sequence)."""
    return math.fsum(values) / len(values) if values else 0.0


def pstdev(values: Sequence[float], *, known_mean: float | None = None) -> float:
    """Population standard deviation (0.0 for fewer than 2 values)."""
    if len(values) < 2:
        return 0.0
    mu = mean(values) if known_mean is None else known_mean
    variance = math.fsum((v - mu) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return low if value < low else high if value > high else value


def safe_log(value: float, *, floor: float = 1e-12) -> float:
    """Natural log with a small floor to avoid ``log(0)``."""
    return math.log(value if value > floor else floor)


def gini_impurity(positives: int, total: int) -> float:
    """Gini impurity of a binary node given the count of positives."""
    if total == 0:
        return 0.0
    p = positives / total
    return 2.0 * p * (1.0 - p)


def normalize(values: Sequence[float]) -> list[float]:
    """Scale non-negative values so they sum to 1 (uniform if all zero)."""
    total = math.fsum(values)
    if total <= 0.0:
        n = len(values)
        return [1.0 / n] * n if n else []
    return [v / total for v in values]
