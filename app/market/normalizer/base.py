"""Utilidades compartidas por los normalizadores de exchange."""

from datetime import UTC, datetime

from app.core.exceptions import NormalizationError
from app.utils.time import utc_now


def ts_from_ms(value: float | int | str) -> datetime:
    """Convert an epoch-milliseconds value to an aware UTC datetime.

    Args:
        value: Milisegundos desde epoch (número o string numérico).

    Returns:
        Datetime UTC con ``tzinfo``.

    Raises:
        NormalizationError: Si el valor no es convertible.
    """
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise NormalizationError(
            "Invalid epoch-ms timestamp", context={"value": repr(value)}
        ) from exc


def as_float(value: object, field: str) -> float:
    """Parse a numeric field defensively.

    Args:
        value: Valor crudo (string o número).
        field: Nombre del campo (para el contexto del error).

    Returns:
        El valor como ``float``.

    Raises:
        NormalizationError: Si no es numérico.
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise NormalizationError(
            "Non-numeric field in exchange message",
            context={"field": field, "value": repr(value)},
        ) from exc


def local_now() -> datetime:
    """Local reception timestamp (UTC). Alias semántico de ``utc_now``."""
    return utc_now()
