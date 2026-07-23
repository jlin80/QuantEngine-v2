"""Monitor de rendimiento del modelo en vivo (Fase 7).

Registra pares (probabilidad predicha, resultado real) conforme se resuelven las
operaciones y calcula métricas móviles (accuracy, AUC). Es la base de la
detección de *performance drift*: cuando el rendimiento vivo cae respecto a la
línea base con la que se validó el modelo, se levanta la alerta.
"""

from collections import deque
from typing import Any

from app.ml.evaluation.metrics import evaluate


class ModelPerformanceMonitor:
    """Rolling accuracy/AUC of the active model against realised outcomes.

    Args:
        window: Tamaño de la ventana móvil de observaciones.
        baseline_auc: AUC de validación con la que se aprobó el modelo.
    """

    def __init__(self, *, window: int = 200, baseline_auc: float | None = None) -> None:
        self._proba: deque[float] = deque(maxlen=window)
        self._outcomes: deque[int] = deque(maxlen=window)
        self._baseline_auc = baseline_auc

    def set_baseline(self, auc: float) -> None:
        """Set the validation baseline AUC for degradation checks."""
        self._baseline_auc = auc

    def record(self, probability: float, outcome: int) -> None:
        """Record a prediction and its realised binary outcome."""
        self._proba.append(float(probability))
        self._outcomes.append(int(outcome))

    @property
    def samples(self) -> int:
        """Number of observations currently in the window."""
        return len(self._outcomes)

    def metrics(self) -> dict[str, Any]:
        """Rolling metrics over the observation window."""
        if not self._outcomes:
            return {"samples": 0, "accuracy": 0.0, "auc": 0.5, "positives": 0}
        result = evaluate(list(self._outcomes), list(self._proba))
        data = result.to_dict()
        data["baseline_auc"] = self._baseline_auc
        return data

    def is_degraded(self, *, min_samples: int = 30, drop: float = 0.10) -> bool:
        """Whether live AUC has fallen below baseline by at least ``drop``."""
        if self._baseline_auc is None or self.samples < min_samples:
            return False
        current = evaluate(list(self._outcomes), list(self._proba)).auc
        return (self._baseline_auc - current) >= drop
