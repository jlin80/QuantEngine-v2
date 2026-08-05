"""Modelos de la atribución de edge (Bloque 2).

Dos piezas: la **foto** de los factores en el momento de decidir
(:class:`FactorSnapshot`) y la **explicación** de una operación ya cerrada
(:class:`TradeAttribution`), que sale de cruzar esa foto con el resultado.

Advertencia que recorre todo el módulo y que no se debe perder al leer sus
números: esto mide **asociación histórica, no causa**. Decir "esta operación
ganó *por* el order flow" es una afirmación causal que una muestra observacional
no sostiene. Lo que sí se puede afirmar —y es lo que se calcula— es "en esta
operación el order flow estaba en el tercio alto, y las operaciones con el order
flow en el tercio alto han rendido X R más que las del tercio bajo". Por eso la
métrica se llama ``lift`` y por eso el informe reporta siempre el residuo.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class FactorSnapshot:
    """Estado de los factores en el instante de una decisión.

    Attributes:
        decision_id: Decisión fotografiada. Es la clave del join con el trade.
        symbol: Activo.
        at: Momento de la captura.
        numeric: Factores continuos (order flow, VWAP, momentum, delta, CVD,
            liquidez, confirmaciones, ML…). ``None`` cuando el factor no era
            observable: no se rellena con ceros, que se confundirían con
            "medido y sale cero".
        labels: Factores categóricos (estrategia, sesión, régimen, volatilidad).
        accepted: Si la decisión terminó en orden.
    """

    decision_id: str
    symbol: str
    at: datetime
    numeric: dict[str, float | None] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "at": self.at.isoformat(),
            "numeric": self.numeric,
            "labels": self.labels,
            "accepted": self.accepted,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FactorSnapshot":
        """Rebuild a snapshot from one persisted row.

        Args:
            data: Fila ya parseada del store.

        Returns:
            La foto reconstruida.

        Raises:
            KeyError: Si falta ``decision_id`` o ``at``.
            ValueError: Si la marca temporal no es interpretable.
        """
        numeric_raw = data.get("numeric", {})
        return cls(
            decision_id=str(data["decision_id"]),
            symbol=str(data.get("symbol", "")),
            at=datetime.fromisoformat(str(data["at"])),
            numeric={
                str(key): (None if value is None else float(value))
                for key, value in numeric_raw.items()
            },
            labels={str(key): str(value) for key, value in data.get("labels", {}).items()},
            accepted=bool(data.get("accepted", False)),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class FactorContribution:
    """Aporte atribuido a un factor en una operación concreta.

    Attributes:
        factor: Nombre del factor.
        kind: ``numeric`` o ``label``.
        value: Valor observado (numérico) o ``None`` en categóricos.
        bucket: Tercil (``low``/``mid``/``high``) o la etiqueta observada.
        lift_r: R media del bucket menos la R media global de la muestra. Es la
            asociación histórica del bucket con el resultado, **no** una causa.
        sample: Operaciones del bucket que sostienen ese ``lift_r``.
    """

    factor: str
    kind: str
    value: float | None
    bucket: str
    lift_r: float
    sample: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "factor": self.factor,
            "kind": self.kind,
            "value": None if self.value is None else round(self.value, 6),
            "bucket": self.bucket,
            "lift_r": round(self.lift_r, 4),
            "sample": self.sample,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class TradeAttribution:
    """Explicación de una operación cerrada, factor a factor.

    Attributes:
        trade_id: Operación explicada.
        decision_id: Decisión de origen (``None`` si el trade no la traía).
        symbol: Activo.
        strategy: Estrategia atribuida.
        r_multiple: Resultado real, en R.
        pnl: Resultado real, en dinero.
        baseline_r: R media de la muestra usada como referencia.
        contributions: Aporte por factor, de mayor a menor magnitud.
        explained_r: Suma de los ``lift_r``. **No** es una descomposición
            exacta: los factores están correlacionados entre sí, así que sus
            asociaciones no son aditivas. Es una lectura de qué evidencia
            acompañaba a la operación, con qué signo y con cuánta muestra.
        residual_r: Lo que la suma anterior no explica. Se reporta siempre y a
            propósito: un residuo grande es la señal de que la explicación no
            explica, y ocultarlo convertiría el informe en una narración.
    """

    trade_id: str
    decision_id: str | None
    symbol: str
    strategy: str
    r_multiple: float
    pnl: float
    baseline_r: float
    contributions: tuple[FactorContribution, ...] = ()

    @property
    def explained_r(self) -> float:
        """Suma de asociaciones, no una descomposición exacta."""
        return sum(c.lift_r for c in self.contributions)

    @property
    def residual_r(self) -> float:
        """Parte del resultado que la asociación no explica."""
        return self.r_multiple - self.baseline_r - self.explained_r

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "trade_id": self.trade_id,
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "r_multiple": round(self.r_multiple, 4),
            "pnl": round(self.pnl, 4),
            "baseline_r": round(self.baseline_r, 4),
            "explained_r": round(self.explained_r, 4),
            "residual_r": round(self.residual_r, 4),
            "contributions": [c.to_dict() for c in self.contributions],
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class FactorEdge:
    """Asociación agregada de un factor con el resultado.

    Attributes:
        factor: Nombre del factor.
        kind: ``numeric`` o ``label``.
        buckets: Por bucket, ``{sample, mean_r, win_rate, lift_r}``.
        spread_r: Diferencia de R media entre el mejor y el peor bucket. Es la
            medida de cuánto discrimina el factor.
        sample: Operaciones con este factor observable.
        low_cut: Corte inferior de los terciles (sólo factores continuos). Se
            guarda con el agregado porque explicar una operación exige
            reasignarla al mismo bucket con el que se midió; recalcular los
            cortes al explicar produciría una explicación que no concuerda con
            el informe del que sale.
        high_cut: Corte superior de los terciles.
    """

    factor: str
    kind: str
    buckets: dict[str, dict[str, float]] = field(default_factory=dict)
    spread_r: float = 0.0
    sample: int = 0
    low_cut: float | None = None
    high_cut: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "factor": self.factor,
            "kind": self.kind,
            "buckets": self.buckets,
            "spread_r": round(self.spread_r, 4),
            "sample": self.sample,
            "low_cut": self.low_cut,
            "high_cut": self.high_cut,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class AttributionReport:
    """Informe agregado: qué factores acompañan a los buenos resultados.

    Attributes:
        generated_at: Momento del cálculo.
        trades: Operaciones que entraron en la muestra.
        matched: Operaciones que además tenían foto de factores.
        baseline_r: R media de la muestra.
        factors: Asociación por factor, ordenada por poder discriminante.
        join_breakdown: Por qué quedó fuera lo que quedó fuera. Se reporta con
            números, no se silencia: sin esto no se puede saber si el informe
            habla de la mitad de las operaciones o de todas.
    """

    generated_at: datetime = field(default_factory=utc_now)
    trades: int = 0
    matched: int = 0
    baseline_r: float = 0.0
    factors: tuple[FactorEdge, ...] = ()
    join_breakdown: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "trades": self.trades,
            "matched": self.matched,
            "baseline_r": round(self.baseline_r, 4),
            "factors": [f.to_dict() for f in self.factors],
            "join_breakdown": self.join_breakdown,
            # Recordatorio deliberado en la propia carga útil: este informe se
            # va a leer en un dashboard, fuera del contexto de la doc.
            "caveat": (
                "asociación histórica, no causa; los factores están "
                "correlacionados y sus aportes no son aditivos"
            ),
        }
