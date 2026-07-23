"""Métricas de clasificación (Python puro) para evaluar modelos de ML.

Accuracy, precision, recall, F1, matriz de confusión, AUC (ROC) y log-loss. El
AUC se calcula por el estadístico de rangos (equivalente a Mann-Whitney U), sin
integrar curvas: exacto y sin dependencias.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.ml.models.math import safe_log


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    """Bundle of binary-classification metrics for a model evaluation."""

    samples: int
    positives: int
    accuracy: float
    precision: float
    recall: float
    f1: float
    auc: float
    log_loss: float
    tp: int
    tn: int
    fp: int
    fn: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (rounded for readability)."""
        return {
            "samples": self.samples,
            "positives": self.positives,
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "auc": round(self.auc, 4),
            "log_loss": round(self.log_loss, 4),
            "confusion": {"tp": self.tp, "tn": self.tn, "fp": self.fp, "fn": self.fn},
        }


def _rank(values: Sequence[float]) -> list[float]:
    """Average ranks (1-based) of ``values``, handling ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0  # ranks are 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def roc_auc(y_true: Sequence[int], scores: Sequence[float]) -> float:
    """Area under the ROC curve via the rank statistic (0.5 if degenerate)."""
    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    ranks = _rank(scores)
    sum_pos = sum(rank for rank, y in zip(ranks, y_true, strict=False) if y == 1)
    return (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def log_loss(y_true: Sequence[int], proba: Sequence[float]) -> float:
    """Binary cross-entropy (lower is better)."""
    if not y_true:
        return 0.0
    total = 0.0
    for y, p in zip(y_true, proba, strict=False):
        p = min(max(p, 1e-15), 1.0 - 1e-15)
        total += -(y * safe_log(p) + (1 - y) * safe_log(1.0 - p))
    return total / len(y_true)


def evaluate(
    y_true: Sequence[int], proba: Sequence[float], *, threshold: float = 0.5
) -> ClassificationMetrics:
    """Compute the full metric bundle for probabilistic predictions.

    Args:
        y_true: Etiquetas reales (0/1).
        proba: Probabilidad predicha de clase 1.
        threshold: Umbral de decisión para las métricas de conteo.

    Returns:
        Un :class:`ClassificationMetrics` con todas las métricas.
    """
    n = len(y_true)
    tp = tn = fp = fn = 0
    for y, p in zip(y_true, proba, strict=False):
        pred = 1 if p >= threshold else 0
        if pred == 1 and y == 1:
            tp += 1
        elif pred == 0 and y == 0:
            tn += 1
        elif pred == 1 and y == 0:
            fp += 1
        else:
            fn += 1
    accuracy = (tp + tn) / n if n else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return ClassificationMetrics(
        samples=n,
        positives=sum(y_true),
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
        auc=roc_auc(y_true, proba),
        log_loss=log_loss(y_true, proba),
        tp=tp,
        tn=tn,
        fp=fp,
        fn=fn,
    )
