"""Contrato base de todos los eventos del sistema.

Los eventos son inmutables (dataclasses congeladas), auto-identificados y
serializables. Los módulos se comunican publicándolos en el
:class:`~app.core.events.bus.EventBus` — nunca mediante llamadas directas
cuando un evento sea suficiente.
"""

import dataclasses
import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import utc_now


def _new_event_id() -> str:
    """Generate a unique event identifier."""
    return uuid.uuid4().hex


def _json_safe(value: Any) -> Any:
    """Convert datetimes/enums (recursivamente) a valores JSON-serializables."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


@dataclass(frozen=True, kw_only=True, slots=True)
class Event:
    """Base immutable event.

    Attributes:
        event_id: Unique identifier for tracing/deduplication.
        occurred_at: UTC timestamp of when the event was created.
        source: Name of the module that published the event.
    """

    event_id: str = field(default_factory=_new_event_id)
    occurred_at: datetime = field(default_factory=utc_now)
    source: str = "system"

    @property
    def name(self) -> str:
        """Event type name (class name), used for routing and logs."""
        return type(self).__name__

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event to a JSON-friendly dictionary.

        Todos los campos anidados (datetimes, enums, colecciones) se
        convierten a tipos JSON-serializables.

        Returns:
            Dict with every field plus the ``event`` type name.
        """
        payload: dict[str, Any] = _json_safe(dataclasses.asdict(self))
        payload["event"] = self.name
        return payload
