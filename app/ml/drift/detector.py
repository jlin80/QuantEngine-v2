"""Detección de deriva del modelo y de los datos (Fase 7).

Cuatro clases de deriva:

* **Feature drift** — la distribución de las variables cambia (Population
  Stability Index por columna).
* **Concept drift** — cambia la tasa de aciertos (relación variable→resultado).
* **Performance drift** — el rendimiento vivo cae respecto a la línea base.
* **Model drift** — degradación sostenida del modelo activo (performance drift
  aplicado al modelo en producción).

Ante deriva: se genera alerta, se reduce la confianza y se programa
reentrenamiento. Nunca se para la operativa: sólo se ajusta el asesoramiento.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import MLDriftSettings
from app.ml.datasets.dataset import Dataset
from app.ml.models.math import safe_log


@dataclass(frozen=True, slots=True)
class DriftSignal:
    """A single detected drift condition."""

    kind: str  # "feature" | "concept" | "performance" | "model"
    metric: str
    value: float
    threshold: float
    action: str  # "alert" | "reduce_confidence" | "schedule_retrain"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "kind": self.kind,
            "metric": self.metric,
            "value": round(self.value, 4),
            "threshold": self.threshold,
            "action": self.action,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Aggregate drift report across all checks."""

    signals: list[DriftSignal] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        """Whether any drift was detected."""
        return bool(self.signals)

    @property
    def should_retrain(self) -> bool:
        """Whether any signal recommends retraining."""
        return any(s.action == "schedule_retrain" for s in self.signals)

    def confidence_factor(self, reduce_to: float) -> float:
        """Confidence multiplier to apply given the detected drift."""
        return reduce_to if self.has_drift else 1.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "has_drift": self.has_drift,
            "should_retrain": self.should_retrain,
            "signals": [s.to_dict() for s in self.signals],
        }


def population_stability_index(
    reference: Sequence[float], current: Sequence[float], *, bins: int = 10
) -> float:
    """Population Stability Index between two samples (0 = identical).

    Args:
        reference: Muestra de referencia (entrenamiento).
        current: Muestra actual (producción).
        bins: Número de tramos (por cuantiles de la referencia).

    Returns:
        El PSI (mayor = más deriva). 0.0 si no hay datos suficientes.
    """
    if len(reference) < bins or not current:
        return 0.0
    edges = _quantile_edges(reference, bins)
    if not edges:
        return 0.0
    ref_fracs = _bin_fractions(reference, edges)
    cur_fracs = _bin_fractions(current, edges)
    psi = 0.0
    for ref_f, cur_f in zip(ref_fracs, cur_fracs, strict=False):
        ref_f = max(ref_f, 1e-6)
        cur_f = max(cur_f, 1e-6)
        psi += (cur_f - ref_f) * safe_log(cur_f / ref_f)
    return psi


class DriftDetector:
    """Detect feature/concept/performance/model drift from evidence.

    Args:
        settings: Umbrales de deriva (PSI y caída de rendimiento).
    """

    def __init__(self, settings: MLDriftSettings) -> None:
        self._settings = settings

    def feature_drift(self, reference: Dataset, current: Dataset) -> list[DriftSignal]:
        """PSI per feature between the training and current datasets."""
        signals: list[DriftSignal] = []
        if len(current) < self._settings.min_samples:
            return signals
        for j, name in enumerate(reference.feature_names):
            ref_col = [row[j] for row in reference.x]
            cur_col = [row[j] for row in current.x if j < len(row)]
            psi = population_stability_index(ref_col, cur_col)
            if psi >= self._settings.psi_alert:
                signals.append(
                    DriftSignal(
                        kind="feature",
                        metric=f"psi:{name}",
                        value=psi,
                        threshold=self._settings.psi_alert,
                        action="schedule_retrain",
                        detail=f"La distribución de '{name}' cambió (PSI {psi:.2f}).",
                    )
                )
            elif psi >= self._settings.psi_warning:
                signals.append(
                    DriftSignal(
                        kind="feature",
                        metric=f"psi:{name}",
                        value=psi,
                        threshold=self._settings.psi_warning,
                        action="reduce_confidence",
                        detail=f"Ligera deriva de '{name}' (PSI {psi:.2f}).",
                    )
                )
        return signals

    def concept_drift(self, reference: Dataset, current: Dataset) -> DriftSignal | None:
        """Shift in the positive (win) rate between reference and current."""
        if len(current) < self._settings.min_samples:
            return None
        shift = abs(current.balance - reference.balance)
        threshold = max(self._settings.performance_drop, 0.10)
        if shift >= threshold:
            return DriftSignal(
                kind="concept",
                metric="win_rate_shift",
                value=shift,
                threshold=threshold,
                action="schedule_retrain",
                detail=(
                    f"La tasa de aciertos pasó de {reference.balance:.0%} a "
                    f"{current.balance:.0%}."
                ),
            )
        return None

    def performance_drift(
        self, baseline: float, current: float, *, kind: str = "performance"
    ) -> DriftSignal | None:
        """Drop of a live metric (AUC) below its validation baseline."""
        drop = baseline - current
        if drop >= self._settings.performance_drop:
            return DriftSignal(
                kind=kind,
                metric="auc",
                value=drop,
                threshold=self._settings.performance_drop,
                action="schedule_retrain",
                detail=f"AUC vivo {current:.2f} vs línea base {baseline:.2f}.",
            )
        return None

    def analyze(
        self,
        reference: Dataset,
        current: Dataset,
        *,
        baseline_auc: float | None = None,
        live_auc: float | None = None,
    ) -> DriftReport:
        """Run every applicable drift check and build a report."""
        signals = self.feature_drift(reference, current)
        concept = self.concept_drift(reference, current)
        if concept is not None:
            signals.append(concept)
        if baseline_auc is not None and live_auc is not None:
            perf = self.performance_drift(baseline_auc, live_auc, kind="model")
            if perf is not None:
                signals.append(perf)
        return DriftReport(signals=signals)


def _quantile_edges(values: Sequence[float], bins: int) -> list[float]:
    """Interior quantile edges of ``values`` (deduplicated)."""
    ordered = sorted(values)
    n = len(ordered)
    edges: list[float] = []
    for i in range(1, bins):
        idx = min(n - 1, round(i / bins * n))
        edges.append(ordered[idx])
    unique = sorted(set(edges))
    return unique


def _bin_fractions(values: Sequence[float], edges: Sequence[float]) -> list[float]:
    """Fraction of ``values`` falling in each bin defined by ``edges``."""
    counts = [0] * (len(edges) + 1)
    for value in values:
        placed = False
        for i, edge in enumerate(edges):
            if value <= edge:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    total = len(values) or 1
    return [count / total for count in counts]
