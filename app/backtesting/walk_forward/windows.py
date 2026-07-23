"""Generación de ventanas para Walk Forward Analysis (Fase 6).

Divide una serie en tramos de entrenamiento (in-sample) y validación
(out-of-sample) según el esquema elegido: rolling (ventana deslizante de tamaño
fijo), expanding/anchored (entrenamiento anclado al inicio que crece). Regla de
oro del análisis: nunca se optimiza sobre todo el histórico — siempre queda un
tramo out-of-sample que la optimización no vio.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Window:
    """Un pliegue de walk-forward, en índices sobre la serie de velas.

    Attributes:
        index: Número de pliegue (0-based).
        train_start: Inicio del tramo de entrenamiento (inclusivo).
        train_end: Fin del entrenamiento (exclusivo).
        test_start: Inicio de la validación out-of-sample (inclusivo).
        test_end: Fin de la validación (exclusivo).
    """

    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_size(self) -> int:
        """Número de velas de entrenamiento."""
        return self.train_end - self.train_start

    @property
    def test_size(self) -> int:
        """Número de velas de validación."""
        return self.test_end - self.test_start

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "index": self.index,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "train_size": self.train_size,
            "test_size": self.test_size,
        }


def generate_windows(
    total: int,
    *,
    scheme: str = "rolling",
    train_size: int,
    validation_size: int,
    step: int,
) -> list[Window]:
    """Generate walk-forward folds over a series of ``total`` candles.

    Args:
        total: Longitud de la serie.
        scheme: ``rolling`` | ``expanding`` | ``anchored``.
        train_size: Velas de entrenamiento (tamaño inicial en expanding).
        validation_size: Velas de validación por pliegue.
        step: Avance entre pliegues.

    Returns:
        Lista de pliegues; vacía si la serie no da ni para un pliegue.

    Raises:
        ValueError: Si algún tamaño o el paso no son positivos, o el esquema
            es desconocido.
    """
    if train_size <= 0 or validation_size <= 0 or step <= 0:
        raise ValueError("train_size, validation_size y step deben ser positivos")
    normalized = "expanding" if scheme == "anchored" else scheme
    if normalized not in ("rolling", "expanding"):
        raise ValueError(f"Esquema de walk-forward desconocido: {scheme}")

    windows: list[Window] = []
    fold = 0
    train_end = train_size
    while train_end + validation_size <= total:
        train_start = train_end - train_size if normalized == "rolling" else 0
        windows.append(
            Window(
                index=fold,
                train_start=train_start,
                train_end=train_end,
                test_start=train_end,
                test_end=train_end + validation_size,
            )
        )
        fold += 1
        train_end += step
    return windows
