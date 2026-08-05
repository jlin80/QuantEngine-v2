"""Confidence Calibration Engine (Bloque 10) — ¿la confianza es real?

Una confianza de 0.8 debería significar que, de cada diez veces que el sistema
dice 0.8, acierta ocho. Casi nunca es así: los modelos y los heurísticos tienden
a la **sobreconfianza**, y nadie lo nota porque la confianza se mira junto al
resultado individual, donde no hay forma de verla fallar. Sólo se ve en
agregado, y sólo si alguien la mide.

Este motor la mide: curva de calibración, diagrama de fiabilidad, error de
calibración esperado (ECE), Brier score y el sesgo con su signo. Y devuelve al
ML una corrección aplicable, para que medirlo sirva de algo.

**Lo que no hace.** No arregla la confianza por su cuenta ni la reescribe en la
decisión. Devuelve el factor de corrección y quién lo consuma decide; una capa
que corrigiera silenciosamente su propia entrada haría imposible saber si el
modelo mejoró o si sólo se le está tapando el error.
"""

import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import MLCalibrationSettings
from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class CalibrationBin:
    """Un tramo de confianza con lo que realmente pasó dentro.

    Attributes:
        lower: Límite inferior del tramo.
        upper: Límite superior.
        sample: Observaciones en el tramo.
        mean_confidence: Confianza media declarada.
        observed_rate: Fracción de aciertos real.
        gap: ``observed - declarada``. Negativo = sobreconfianza.
    """

    lower: float
    upper: float
    sample: int
    mean_confidence: float
    observed_rate: float

    @property
    def gap(self) -> float:
        """Distancia entre lo prometido y lo cumplido."""
        return self.observed_rate - self.mean_confidence

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "lower": round(self.lower, 4),
            "upper": round(self.upper, 4),
            "sample": self.sample,
            "mean_confidence": round(self.mean_confidence, 4),
            "observed_rate": round(self.observed_rate, 4),
            "gap": round(self.gap, 4),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class CalibrationReport:
    """Diagnóstico completo de si la confianza declarada se cumple.

    Attributes:
        generated_at: Momento del cálculo.
        sample: Observaciones usadas.
        bins: Curva de calibración (cada tramo con su muestra).
        ece: Error de calibración esperado, ponderado por muestra.
        brier: Brier score de las predicciones binarias.
        bias: Sesgo global (``acierto real - confianza media``).
        overconfidence: Cuánto promete de más, cuando promete de más.
        underconfidence: Cuánto promete de menos.
        correction: Factor multiplicativo sugerido para la confianza. 1.0 = no
            hace falta corregir.
        observable: Si había muestra suficiente para concluir algo.
        reason: Por qué no, si no la había.
    """

    generated_at: datetime = field(default_factory=utc_now)
    sample: int = 0
    bins: tuple[CalibrationBin, ...] = ()
    ece: float | None = None
    brier: float | None = None
    bias: float | None = None
    overconfidence: float | None = None
    underconfidence: float | None = None
    correction: float = 1.0
    observable: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "sample": self.sample,
            "bins": [b.to_dict() for b in self.bins],
            "ece": _round(self.ece),
            "brier": _round(self.brier),
            "bias": _round(self.bias),
            "overconfidence": _round(self.overconfidence),
            "underconfidence": _round(self.underconfidence),
            "correction": round(self.correction, 4),
            "observable": self.observable,
            "reason": self.reason,
            # El diagrama de fiabilidad es la curva más la diagonal perfecta:
            # se sirve ya emparejado para que el dashboard no tenga que
            # reconstruir la referencia y arriesgarse a dibujarla mal.
            "reliability_diagram": [
                {"declared": round(b.mean_confidence, 4), "observed": round(b.observed_rate, 4)}
                for b in self.bins
            ],
        }


class ConfidenceCalibrationEngine:
    """Measure whether declared confidence matches observed outcomes.

    Args:
        settings: Tramos, mínimos y límites de la corrección.
    """

    def __init__(self, settings: MLCalibrationSettings) -> None:
        self._settings = settings
        self._observations: deque[tuple[float, bool]] = deque(maxlen=settings.window)
        self._last: CalibrationReport | None = None
        self._log = logging.getLogger("app.ml.calibration")

    def observe(self, confidence: float, success: bool) -> None:
        """Record one (declared confidence → real outcome) pair.

        Args:
            confidence: Confianza declarada, 0-1.
            success: Si la operación salió bien.
        """
        self._observations.append((max(0.0, min(1.0, confidence)), success))

    def observe_many(self, pairs: Sequence[tuple[float, bool]]) -> int:
        """Record a batch of observations.

        Args:
            pairs: Pares ``(confianza, acierto)``.

        Returns:
            Cuántas se incorporaron.
        """
        for confidence, success in pairs:
            self.observe(confidence, success)
        return len(pairs)

    def analyze(self) -> CalibrationReport:
        """Build the full calibration report.

        Returns:
            El informe. Sin muestra suficiente devuelve ``observable=False`` y
            **corrección 1.0**: sin evidencia no se toca la confianza de nadie.
        """
        observations = list(self._observations)
        if len(observations) < self._settings.min_sample:
            report = CalibrationReport(
                generated_at=utc_now(),
                sample=len(observations),
                reason=(f"muestra {len(observations)} < mínimo {self._settings.min_sample}"),
            )
            self._last = report
            return report

        bins = _build_bins(observations, self._settings.bins, self._settings.min_bin_sample)
        total = len(observations)
        ece = sum(b.sample * abs(b.gap) for b in bins) / total if bins else None
        brier = sum((c - (1.0 if s else 0.0)) ** 2 for c, s in observations) / total
        mean_confidence = sum(c for c, _ in observations) / total
        observed_rate = sum(1 for _, s in observations if s) / total
        bias = observed_rate - mean_confidence
        over = sum(b.sample * -b.gap for b in bins if b.gap < 0) / total if bins else None
        under = sum(b.sample * b.gap for b in bins if b.gap > 0) / total if bins else None

        report = CalibrationReport(
            generated_at=utc_now(),
            sample=total,
            bins=bins,
            ece=ece,
            brier=brier,
            bias=bias,
            overconfidence=over,
            underconfidence=under,
            correction=self._correction(mean_confidence, observed_rate),
            observable=True,
        )
        self._last = report
        return report

    def _correction(self, mean_confidence: float, observed_rate: float) -> float:
        """Multiplicative correction that would align declared with observed.

        Se acota por configuración porque una corrección sin techo convierte una
        racha en un recalibrado brutal: con 60 operaciones, un mes malo puede
        producir un factor de 0.4 que apagaría medio sistema. El techo hace que
        la corrección sea un empujón, no un volantazo.
        """
        if mean_confidence <= 1e-9:
            return 1.0
        raw = observed_rate / mean_confidence
        return max(self._settings.min_correction, min(self._settings.max_correction, raw))

    def calibrated(self, confidence: float) -> float:
        """Apply the current correction to one confidence value.

        Args:
            confidence: Confianza declarada.

        Returns:
            La confianza corregida, acotada a 0-1. Sin informe observable
            devuelve el valor tal cual: no corregir es la única opción honesta
            cuando no se ha medido nada.
        """
        if self._last is None or not self._last.observable:
            return confidence
        return max(0.0, min(1.0, confidence * self._last.correction))

    def last_report(self) -> CalibrationReport | None:
        """Informe del último análisis."""
        return self._last

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "observations": len(self._observations),
            "last_report": None if self._last is None else self._last.to_dict(),
        }


def _build_bins(
    observations: Sequence[tuple[float, bool]], count: int, min_sample: int
) -> tuple[CalibrationBin, ...]:
    """Group observations into equal-width confidence bins.

    Los tramos con menos muestra que ``min_sample`` se descartan: un tramo con
    tres observaciones produce una tasa observada de 0.0 o 0.67 y arrastra el
    ECE con un ruido que no significa nada.
    """
    if count < 1:
        return ()
    width = 1.0 / count
    buckets: dict[int, list[tuple[float, bool]]] = {}
    for confidence, success in observations:
        index = min(count - 1, int(confidence / width))
        buckets.setdefault(index, []).append((confidence, success))
    bins: list[CalibrationBin] = []
    for index in sorted(buckets):
        items = buckets[index]
        if len(items) < min_sample:
            continue
        bins.append(
            CalibrationBin(
                lower=index * width,
                upper=(index + 1) * width,
                sample=len(items),
                mean_confidence=sum(c for c, _ in items) / len(items),
                observed_rate=sum(1 for _, s in items if s) / len(items),
            )
        )
    return tuple(bins)


def _round(value: float | None, digits: int = 4) -> float | None:
    """Round without turning ``None`` into a number."""
    return None if value is None else round(value, digits)
