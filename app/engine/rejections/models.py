"""Modelos del Why Not Trade Engine (Bloque 14).

El motor ya explicaba sus rechazos en texto, dentro de ``Decision.explanation``.
El texto sirve para leer *una* decisión y no sirve para nada más: no se puede
agregar, no se puede contar, no se puede responder "¿qué filtro me está costando
más operaciones este mes?". Esto convierte esa explicación en datos.

**Sobre las "penalizaciones".** Los filtros de este motor no restan puntos: vetan.
Modelarlos como si aplicaran una penalización parcial sería inventarse una
aritmética que el sistema no tiene. Lo que se registra es lo que de verdad
ocurre: qué umbral no se alcanzó y por cuánto (eso sí es un déficit medible), y
qué filtro bloqueó (eso es binario, y se registra como binario).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class GateResult:
    """Resultado de un umbral o de un filtro sobre una decisión.

    Attributes:
        name: Nombre del umbral o filtro.
        kind: ``threshold`` (tiene valor y mínimo) o ``filter`` (veta o pasa).
        passed: Si lo superó.
        value: Valor observado (sólo umbrales).
        required: Mínimo exigido (sólo umbrales).
        deficit: ``required - value`` cuando no se alcanza. Es la única
            "penalización" real que existe aquí, y sólo la tienen los umbrales:
            un filtro no resta, veta.
        reason: Por qué falló.
    """

    name: str
    kind: str
    passed: bool
    value: float | None = None
    required: float | None = None
    reason: str = ""

    @property
    def deficit(self) -> float | None:
        """Cuánto faltó para superar el umbral (``None`` si no aplica)."""
        if self.passed or self.value is None or self.required is None:
            return None
        return self.required - self.value

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "kind": self.kind,
            "passed": self.passed,
            "value": None if self.value is None else round(self.value, 6),
            "required": None if self.required is None else round(self.required, 6),
            "deficit": None if self.deficit is None else round(self.deficit, 6),
            "reason": self.reason,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class RejectionRecord:
    """Por qué no se operó una oportunidad concreta.

    Attributes:
        decision_id: Decisión rechazada.
        symbol: Activo.
        at: Momento.
        initial_score: Score del consenso, antes de umbrales y filtros.
        final_score: Score que sobrevivió. Es 0.0 cuando algo vetó: no se
            opera, así que el score efectivo de la oportunidad es cero.
        confidence: Confianza declarada.
        direction: Dirección propuesta por el consenso.
        gates: Cada umbral y cada filtro, con su resultado.
        blocked_by: Nombres de los que fallaron, en orden de evaluación.
        primary_reason: El primer motivo — el que hay que leer si sólo se lee uno.
        evidence: Contexto y desglose de confianza en el momento del rechazo.
    """

    decision_id: str
    symbol: str
    at: datetime = field(default_factory=utc_now)
    initial_score: float = 0.0
    confidence: float = 0.0
    direction: str = ""
    gates: tuple[GateResult, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked_by(self) -> tuple[str, ...]:
        """Umbrales y filtros que no se superaron."""
        return tuple(gate.name for gate in self.gates if not gate.passed)

    @property
    def final_score(self) -> float:
        """Score efectivo tras los vetos."""
        return 0.0 if self.blocked_by else self.initial_score

    @property
    def primary_reason(self) -> str:
        """El motivo que hay que leer si sólo se lee uno."""
        for gate in self.gates:
            if not gate.passed:
                return gate.reason or gate.name
        return ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "at": self.at.isoformat(),
            "initial_score": round(self.initial_score, 4),
            "final_score": round(self.final_score, 4),
            "confidence": round(self.confidence, 4),
            "direction": self.direction,
            "gates": [gate.to_dict() for gate in self.gates],
            "blocked_by": list(self.blocked_by),
            "primary_reason": self.primary_reason,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RejectionRecord":
        """Rebuild a record from one persisted row.

        Args:
            data: Fila ya parseada.

        Returns:
            El registro reconstruido.

        Raises:
            KeyError: Si falta ``decision_id`` o ``at``.
            ValueError: Si la marca temporal no es interpretable.
        """
        return cls(
            decision_id=str(data["decision_id"]),
            symbol=str(data.get("symbol", "")),
            at=datetime.fromisoformat(str(data["at"])),
            initial_score=float(data.get("initial_score", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            direction=str(data.get("direction", "")),
            gates=tuple(
                GateResult(
                    name=str(row.get("name", "")),
                    kind=str(row.get("kind", "filter")),
                    passed=bool(row.get("passed", False)),
                    value=None if row.get("value") is None else float(row["value"]),
                    required=None if row.get("required") is None else float(row["required"]),
                    reason=str(row.get("reason", "")),
                )
                for row in data.get("gates", ())
            ),
            evidence=dict(data.get("evidence", {})),
        )
