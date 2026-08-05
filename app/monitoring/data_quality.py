"""Data Quality Engine (Bloque 11) — cuánto se puede fiar el motor de sus datos.

El incidente del 04/08 dejó la lección escrita: el motor puede quedarse ciego
sin lanzar un solo error. El validador descartaba el 100% de los ticks y el
sistema seguía "funcionando" — el bucle corría, el log no decía nada raro, y el
motor simplemente dejó de operar durante cuatro días.

Este motor mide la salud del dato con ocho señales (calidad del feed, pérdida de
paquetes, calidad de ticks y de libro, datos ausentes, deriva de timestamps,
deriva del reloj y retraso del exchange), las agrega en un **Data Quality
Score** y lo traduce en un multiplicador de riesgo.

**Reducir riesgo, no apagar.** El multiplicador nunca llega a 0: apagar el motor
por una métrica de calidad convertiría un problema de datos en una parada total,
y las paradas totales las decide el kill switch, que existe para eso y tiene
auditoría propia. Aquí se reduce exposición, que es reversible y proporcional.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import DataQualitySettings
from app.utils.time import clock_skew_seconds, utc_now

# Las ocho señales del bloque. Cada una en 0-1, donde 1 es sano.
SIGNALS: tuple[str, ...] = (
    "feed_quality",
    "packet_loss",
    "tick_quality",
    "orderbook_quality",
    "missing_data",
    "timestamp_drift",
    "clock_drift",
    "exchange_lag",
)


@dataclass(frozen=True, kw_only=True, slots=True)
class DataQualityReport:
    """Estado de la calidad del dato y su consecuencia sobre el riesgo.

    Attributes:
        generated_at: Momento de la medición.
        signals: Cada señal observable, en 0-1 (1 = sana).
        missing: Señales no observables, con su motivo.
        score: Data Quality Score 0-100, o ``None`` sin señales.
        risk_multiplier: Multiplicador de exposición en ``[floor, 1.0]``.
        degraded: Si el score cae por debajo del umbral de alarma.
        reasons: Qué señales tiraron del score hacia abajo.
    """

    generated_at: datetime = field(default_factory=utc_now)
    signals: dict[str, float] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    score: float | None = None
    risk_multiplier: float = 1.0
    degraded: bool = False
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "signals": {k: round(v, 4) for k, v in self.signals.items()},
            "missing": self.missing,
            "score": None if self.score is None else round(self.score, 2),
            "risk_multiplier": round(self.risk_multiplier, 4),
            "degraded": self.degraded,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class DataQualityInputs:
    """Lo que hay que medir para juzgar la calidad del dato.

    Se pasa como estructura y no como servicios porque este motor tiene que
    poder correr en un test sin montar el Data Engine entero, y porque así el
    monitor no acaba dependiendo de la mitad del sistema.

    Attributes:
        messages: Mensajes recibidos del feed en la ventana.
        rejected: Descartados por el validador.
        dropped: Perdidos por cola llena o desconexión.
        reconnections: Reconexiones en la ventana.
        expected_symbols: Símbolos que deberían estar llegando.
        symbols_with_data: Símbolos de los que sí llega dato.
        books_synced: Libros sincronizados (``None`` si el proveedor no da libro).
        books_total: Libros que deberían estarlo.
        max_timestamp_drift_seconds: Mayor desfase entre el reloj del exchange y
            el local observado.
        exchange_lag_ms: Latencia del dato de extremo a extremo.
    """

    messages: int = 0
    rejected: int = 0
    dropped: int = 0
    reconnections: int = 0
    expected_symbols: int = 0
    symbols_with_data: int = 0
    books_synced: int | None = None
    books_total: int = 0
    max_timestamp_drift_seconds: float | None = None
    exchange_lag_ms: float | None = None


class DataQualityEngine:
    """Score data health and translate it into a risk multiplier.

    Args:
        settings: Umbrales, pesos y suelo del multiplicador.
        inputs_provider: Fuente de las magnitudes a medir.
    """

    def __init__(
        self,
        settings: DataQualitySettings,
        inputs_provider: Callable[[], DataQualityInputs],
    ) -> None:
        self._settings = settings
        self._inputs = inputs_provider
        self._last: DataQualityReport | None = None
        self._log = logging.getLogger("app.monitoring.data_quality")

    def measure(self) -> DataQualityReport:
        """Take one full measurement of data health.

        Returns:
            El informe, con las señales no observables declaradas y el
            multiplicador de riesgo ya calculado.
        """
        data = self._inputs()
        signals: dict[str, float] = {}
        missing: dict[str, str] = {}

        if data.messages > 0:
            accepted = max(0, data.messages - data.rejected - data.dropped)
            signals["feed_quality"] = accepted / data.messages
            signals["packet_loss"] = 1.0 - min(1.0, data.dropped / data.messages)
            signals["tick_quality"] = 1.0 - min(1.0, data.rejected / data.messages)
        else:
            # Sin mensajes no hay "calidad 0": hay ausencia de medición. Son
            # cosas distintas, y el motor ciego del 04/08 se detecta con la
            # señal `missing_data`, no fingiendo una calidad pésima aquí.
            reason = "sin mensajes en la ventana"
            missing["feed_quality"] = reason
            missing["packet_loss"] = reason
            missing["tick_quality"] = reason

        if data.books_total > 0 and data.books_synced is not None:
            signals["orderbook_quality"] = data.books_synced / data.books_total
        else:
            missing["orderbook_quality"] = "el proveedor no publica libro"

        if data.expected_symbols > 0:
            signals["missing_data"] = data.symbols_with_data / data.expected_symbols
        else:
            missing["missing_data"] = "sin símbolos esperados declarados"

        if data.max_timestamp_drift_seconds is not None:
            signals["timestamp_drift"] = _decay(
                abs(data.max_timestamp_drift_seconds), self._settings.max_timestamp_drift_seconds
            )
        else:
            missing["timestamp_drift"] = "sin timestamps de exchange comparables"

        # La deriva del reloj se mide siempre: es la señal del incidente, y no
        # depende de que llegue un solo dato.
        signals["clock_drift"] = _decay(
            abs(clock_skew_seconds()), self._settings.max_clock_skew_seconds
        )

        if data.exchange_lag_ms is not None:
            signals["exchange_lag"] = _decay(
                data.exchange_lag_ms, self._settings.max_exchange_lag_ms
            )
        else:
            missing["exchange_lag"] = "sin latencia de datos medida"

        score = self._score(signals)
        multiplier, degraded, reasons = self._consequence(score, signals)
        report = DataQualityReport(
            generated_at=utc_now(),
            signals=signals,
            missing=missing,
            score=score,
            risk_multiplier=multiplier,
            degraded=degraded,
            reasons=reasons,
        )
        self._last = report
        return report

    def _score(self, signals: dict[str, float]) -> float | None:
        """Weighted mean of the observable signals only."""
        if not signals:
            return None
        weights = self._settings.weights
        total = sum(float(weights.get(name, 1.0)) for name in signals)
        if total <= 0:
            return None
        weighted = sum(float(weights.get(name, 1.0)) * value for name, value in signals.items())
        return 100.0 * weighted / total

    def _consequence(
        self, score: float | None, signals: dict[str, float]
    ) -> tuple[float, bool, tuple[str, ...]]:
        """Turn the score into a risk multiplier and its justification.

        Dos caminos que se combinan tomando el **más severo**: el score medio y
        las señales críticas. Sin el segundo, un motor ciego (`missing_data=0`)
        o un reloj simulado filtrado (`clock_drift=0`) quedan escondidos detrás
        de siete señales sanas y la media ni se entera — que es exactamente la
        forma que tuvo el fallo del 04/08 de pasar desapercibido cuatro días.
        """
        if score is None:
            # Sin ninguna señal observable no se reduce riesgo: reducirlo
            # significaría castigar por no haber medido, y este motor no puede
            # convertir su propio silencio en una decisión operativa.
            return 1.0, False, ("sin señales observables: no se altera el riesgo",)
        reasons = [
            f"{name} {value:.2f} < mínimo {self._settings.signal_floor}"
            for name, value in sorted(signals.items(), key=lambda item: item[1])
            if value < self._settings.signal_floor
        ]
        floor = self._settings.risk_floor
        threshold = self._settings.degraded_score

        # Camino 1: la media. Lineal entre el umbral de alarma y 100 — por
        # encima no se toca nada, por debajo se reduce de forma proporcional.
        by_score = 1.0
        degraded = False
        if score < threshold:
            by_score = floor + (1.0 - floor) * max(0.0, score) / max(threshold, 1e-9)
            degraded = True
            reasons.insert(0, f"calidad {score:.1f} < umbral {threshold}")

        # Camino 2: las señales críticas, cada una por su cuenta.
        by_critical = 1.0
        for name, critical_floor in self._settings.critical_signals.items():
            value = signals.get(name)
            if value is None or value >= critical_floor:
                continue
            degraded = True
            by_critical = min(by_critical, floor + (1.0 - floor) * value)
            reasons.insert(0, f"señal crítica {name} {value:.2f} < {critical_floor}")

        multiplier = min(by_score, by_critical)
        if not degraded:
            return 1.0, False, tuple(reasons) or (f"calidad {score:.1f}/100",)
        return round(max(floor, min(1.0, multiplier)), 4), True, tuple(reasons)

    def risk_multiplier(self) -> float:
        """Current exposure multiplier (1.0 si aún no se ha medido nada)."""
        return 1.0 if self._last is None else self._last.risk_multiplier

    def last_report(self) -> DataQualityReport | None:
        """Informe de la última medición."""
        return self._last

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "last_report": None if self._last is None else self._last.to_dict(),
        }


def _decay(value: float, limit: float) -> float:
    """Map a "smaller is better" magnitude into a 0-1 health signal."""
    if limit <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - value / limit))
