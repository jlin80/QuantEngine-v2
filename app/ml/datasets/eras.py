"""Segmentación del historial por "eras" de ejecución (saneamiento del ML).

La preocupación que resuelve este módulo, en una frase: **no queremos que el ML
aprenda de los bugs de ejecución que ya arreglamos**.

Si el modelo entrena sobre operaciones en las que el stop estaba mal calculado
(bug de ``contract_size``, pre-27/07), el trailing apretaba mal (pre-29/07) o la
salida por régimen cortaba la tesis antes de tiempo (pre-fix de familias), no
está aprendiendo *"esta señal es mala"* — está aprendiendo *"esta señal es mala
**porque la ejecución la saboteó**"*. El resultado es un modelo que penaliza
contextos que en realidad tenían edge: más torpe, no más inteligente.

Dos mecanismos, ambos declarativos en configuración:

- **Peso por era** (:func:`era_of`, :func:`weight_of`): las operaciones de eras
  con bugs conocidos pesan menos o se excluyen. Un peso de ``0.0`` las saca del
  training set por completo.
- **Etiqueta dual** (:func:`is_signal_verdict`): separa "¿la señal era buena?"
  de "¿la ejecución capturó ese edge?". Una operación que cerró por cambio de
  régimen o por tiempo **nunca llegó a poner a prueba su propia tesis**;
  etiquetarla como señal mala es precisamente el error que se quiere evitar.

Clasificación por **hora de entrada**, no de salida: una operación abierta antes
de un fix corrió bajo las reglas viejas durante casi toda su vida, aunque cerrara
después. Es la lectura conservadora — marca como contaminado más de lo justo, no
menos.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.config.settings import MLDataQualitySettings, MLEraSettings
from app.execution.models.trades import TradeRecord

CLEAN_ERA = "post_fixes"
"""Nombre de la era limpia: historial posterior a todos los fixes conocidos."""


def _boundary(era: MLEraSettings) -> datetime:
    """Parse an era's cut-off date as an aware UTC datetime."""
    return datetime.fromisoformat(era.until).replace(tzinfo=UTC)


def era_of(trade: TradeRecord, settings: MLDataQualitySettings) -> str:
    """Name of the execution era a trade belongs to.

    Args:
        trade: Operación cerrada.
        settings: Configuración de saneamiento (eras y sus fechas).

    Returns:
        El nombre de la primera era cuya fecha de corte la operación no alcanza,
        o :data:`CLEAN_ERA` si es posterior a todas.
    """
    entry = trade.entry_time
    if entry.tzinfo is None:
        entry = entry.replace(tzinfo=UTC)
    for era in settings.eras:
        if entry < _boundary(era):
            return era.name
    return CLEAN_ERA


def weight_of(trade: TradeRecord, settings: MLDataQualitySettings) -> float:
    """Training weight of a trade according to its era.

    ``0.0`` significa "no entra al entrenamiento". Con el saneamiento apagado
    (``enabled=False``) todo pesa igual, que es el comportamiento anterior a
    este bloque — se conserva para poder medir el efecto del cambio.
    """
    if not settings.enabled:
        return 1.0
    name = era_of(trade, settings)
    for era in settings.eras:
        if era.name == name:
            return max(0.0, era.weight)
    return max(0.0, settings.clean_weight)


def is_signal_verdict(trade: TradeRecord, settings: MLDataQualitySettings) -> bool:
    """Whether a trade's outcome actually judges its *signal*.

    Sólo las salidas que resuelven la tesis (objetivo, stop, trailing,
    break-even) dicen algo sobre la calidad de la señal. Un cierre por cambio de
    régimen, por tiempo, por kill switch o manual es una decisión de la
    **ejecución**: la operación se cerró antes de que la tesis se resolviera, así
    que su resultado no es evidencia sobre la señal.
    """
    return trade.exit_reason.value in set(settings.signal_label_exit_reasons)


def era_summary(trades: Sequence[TradeRecord], settings: MLDataQualitySettings) -> dict[str, Any]:
    """Auditable breakdown of a trade history by era.

    Lo consume el reporte del ML y el dashboard: sin esto no se puede responder
    "¿qué datos entraron al entrenamiento y por qué?" sin releer el journal.

    Args:
        trades: Historial de operaciones.
        settings: Configuración de saneamiento.

    Returns:
        Recuento, peso y motivo por era, más los totales del training set.
    """
    reasons = {era.name: era.reason for era in settings.eras}
    weights = {era.name: max(0.0, era.weight) for era in settings.eras}
    weights[CLEAN_ERA] = max(0.0, settings.clean_weight)
    reasons[CLEAN_ERA] = "Historial posterior a todos los fixes conocidos."

    counts: dict[str, int] = {}
    for trade in trades:
        name = era_of(trade, settings)
        counts[name] = counts.get(name, 0) + 1

    breakdown = [
        {
            "era": name,
            "trades": counts.get(name, 0),
            "weight": weights.get(name, settings.clean_weight),
            "excluded": weights.get(name, settings.clean_weight) <= 0.0,
            "reason": reasons.get(name, ""),
        }
        for name in [*(e.name for e in settings.eras), CLEAN_ERA]
    ]
    eligible = sum(row["trades"] for row in breakdown if not row["excluded"])  # type: ignore[misc]
    return {
        "enabled": settings.enabled,
        "total_trades": len(trades),
        "eligible_trades": eligible,
        "excluded_trades": len(trades) - eligible,
        "eras": breakdown,
    }
