"""Feature Importance Tracker (Bloque 13) — qué features siguen aportando.

Mide la importancia de cada feature de forma continua y compara la de **ahora**
con la **histórica**: cambio y decaimiento. Una feature que aportaba mucho y ha
dejado de hacerlo es una señal temprana de que el mercado cambió, y aparece aquí
mucho antes que en el PnL.

**Sobre SHAP, que es lo que pide el enunciado.** No se usa, y conviene explicar
por qué en vez de dejarlo como un hueco. SHAP exige una dependencia binaria
pesada y está pensado para árboles de gradiente y redes; los modelos de este
proyecto son propios (árbol, bosque, regresión logística) y algunos backends
externos son opcionales. Añadir esa dependencia para cubrir una parte del
catálogo, y tener que caer a otra cosa para el resto, daría dos métricas
distintas llamadas igual — que es peor que tener una sola bien entendida.

Lo que se usa es **importancia por permutación**: se baraja una feature y se
mide cuánto empeora el modelo. Es agnóstica del modelo, funciona con todos por
igual, y responde exactamente la pregunta operativa ("¿cuánto me costaría perder
esta feature?"). Se complementa con la importancia nativa del modelo cuando la
expone, y ambas se reportan por separado en vez de mezclarse.
"""

import logging
import random
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import MLImportanceSettings
from app.ml.interfaces.model import Model
from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class FeatureImportance:
    """Importancia de una feature, ahora y frente a su historia.

    Attributes:
        feature: Nombre de la feature.
        current: Importancia por permutación en la última medición.
        native: Importancia declarada por el modelo, si la expone.
        historical: Media de las mediciones anteriores (sin incluir la actual).
        change: ``current - historical``. ``None`` sin historia con que comparar.
        decay: Fracción de la importancia histórica que se ha perdido, 0-1.
            ``None`` si no había importancia que perder o no hay historia.
        samples: Mediciones que sostienen la histórica.
    """

    feature: str
    current: float
    native: float | None = None
    historical: float | None = None
    change: float | None = None
    decay: float | None = None
    samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "feature": self.feature,
            "current": round(self.current, 6),
            "native": None if self.native is None else round(self.native, 6),
            "historical": None if self.historical is None else round(self.historical, 6),
            "change": None if self.change is None else round(self.change, 6),
            "decay": None if self.decay is None else round(self.decay, 4),
            "samples": self.samples,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ImportanceReport:
    """Importancia de todas las features en una medición."""

    generated_at: datetime = field(default_factory=utc_now)
    method: str = "permutation"
    baseline_score: float | None = None
    features: tuple[FeatureImportance, ...] = ()
    observable: bool = False
    reason: str = ""

    @property
    def decaying(self) -> tuple[FeatureImportance, ...]:
        """Features que han perdido parte de lo que aportaban."""
        return tuple(f for f in self.features if f.decay is not None and f.decay > 0.0)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "method": self.method,
            "baseline_score": (
                None if self.baseline_score is None else round(self.baseline_score, 6)
            ),
            "features": [f.to_dict() for f in self.features],
            "decaying": [f.feature for f in self.decaying],
            "observable": self.observable,
            "reason": self.reason,
        }


class FeatureImportanceTracker:
    """Track, over time, how much each feature is actually contributing.

    Args:
        settings: Repeticiones, historia y mínimos.
        rng: Fuente aleatoria inyectable — la permutación es aleatoria, y sin
            una semilla fija dos mediciones consecutivas del mismo modelo dan
            números distintos y el "cambio" mediría el ruido del método.
    """

    def __init__(self, settings: MLImportanceSettings, rng: random.Random | None = None) -> None:
        self._settings = settings
        self._rng = rng or random.Random(settings.seed)
        self._history: dict[str, deque[float]] = {}
        self._last: ImportanceReport | None = None
        self._log = logging.getLogger("app.ml.importance")

    def measure(
        self,
        model: Model,
        x: Sequence[Sequence[float]],
        y: Sequence[int],
        feature_names: Sequence[str] | None = None,
    ) -> ImportanceReport:
        """Measure permutation importance of every feature.

        Args:
            model: Modelo ya entrenado.
            x: Matriz de validación. **No** debe ser la de entrenamiento: medir
                sobre lo que el modelo memorizó infla la importancia de las
                features con las que sobreajustó.
            y: Etiquetas reales.
            feature_names: Nombres, si se conocen.

        Returns:
            El informe. Sin muestra o sin modelo entrenado devuelve
            ``observable=False`` con su motivo.
        """
        if not model.is_fitted:
            return self._empty("el modelo no está entrenado")
        if len(x) < self._settings.min_sample:
            return self._empty(f"muestra {len(x)} < mínimo {self._settings.min_sample}")
        columns = len(x[0]) if x else 0
        if columns == 0:
            return self._empty("sin features en la matriz")

        names = list(feature_names or [f"f{index}" for index in range(columns)])
        baseline = _accuracy(model, x, y)
        native = _native_importances(model, columns)

        entries: list[FeatureImportance] = []
        for index in range(columns):
            drop = self._permutation_drop(model, x, y, index, baseline)
            history = self._history.setdefault(
                names[index], deque(maxlen=self._settings.history_limit)
            )
            previous = list(history)
            historical = sum(previous) / len(previous) if previous else None
            change = None if historical is None else drop - historical
            decay = None
            if historical is not None and historical > 1e-9:
                decay = max(0.0, min(1.0, (historical - drop) / historical))
            entries.append(
                FeatureImportance(
                    feature=names[index],
                    current=drop,
                    native=native[index] if native is not None else None,
                    historical=historical,
                    change=change,
                    decay=decay,
                    samples=len(previous),
                )
            )
            # La historia se actualiza DESPUÉS de comparar: si se añadiera
            # antes, la medición actual entraría en su propia referencia y el
            # cambio saldría sistemáticamente amortiguado.
            history.append(drop)

        report = ImportanceReport(
            generated_at=utc_now(),
            baseline_score=baseline,
            features=tuple(sorted(entries, key=lambda f: -f.current)),
            observable=True,
        )
        self._last = report
        return report

    def _permutation_drop(
        self,
        model: Model,
        x: Sequence[Sequence[float]],
        y: Sequence[int],
        index: int,
        baseline: float,
    ) -> float:
        """How much accuracy is lost when one feature is shuffled.

        Se promedia sobre varias repeticiones porque una sola permutación es
        una muestra de una variable aleatoria: con un solo barajado, features
        irrelevantes salen a veces "importantes" por puro azar del reparto.
        """
        rows = [list(row) for row in x]
        total = 0.0
        for _ in range(max(1, self._settings.repeats)):
            column = [row[index] for row in rows]
            self._rng.shuffle(column)
            shuffled = [list(row) for row in rows]
            for position, value in enumerate(column):
                shuffled[position][index] = value
            total += baseline - _accuracy(model, shuffled, y)
        # El máximo con cero es deliberado: una "importancia negativa" sólo
        # significa que barajar mejoró el modelo por azar, y reportarla como
        # negativa invita a interpretarla como que la feature estorba.
        return max(0.0, total / max(1, self._settings.repeats))

    def _empty(self, reason: str) -> ImportanceReport:
        """Report that explains why nothing could be measured."""
        report = ImportanceReport(generated_at=utc_now(), reason=reason)
        self._last = report
        return report

    def last_report(self) -> ImportanceReport | None:
        """Informe de la última medición."""
        return self._last

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "method": "permutation",
            "tracked_features": len(self._history),
            "last_report": None if self._last is None else self._last.to_dict(),
        }


def _accuracy(model: Model, x: Sequence[Sequence[float]], y: Sequence[int]) -> float:
    """Fraction of correct predictions."""
    if not y:
        return 0.0
    predictions = model.predict(x)
    correct = sum(
        1 for predicted, actual in zip(predictions, y, strict=False) if predicted == actual
    )
    return correct / len(y)


def _native_importances(model: Model, columns: int) -> list[float] | None:
    """Model-declared importances, if it exposes usable ones.

    Se reportan **aparte** de la permutación, no mezcladas: miden cosas
    distintas —cuánto usa el modelo una feature frente a cuánto se pierde si
    desaparece— y promediarlas produciría un número que no responde a ninguna
    de las dos preguntas.
    """
    try:
        values = model.feature_importances()
    except Exception:
        return None
    if len(values) != columns:
        return None
    return [float(v) for v in values]
