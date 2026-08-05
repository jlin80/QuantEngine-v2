"""Registro de la microestructura en el Feature Store (Bloque 3).

Se registra desde fuera del `FeatureStore` a propósito: el store es una pieza
de la Fase 3 que no debe conocer el motor de microestructura, y el motor no
debe conocer el store. El composition root los junta — mismo patrón que el
resto de integraciones del proyecto.

Las features escalares devuelven ``None`` cuando la microestructura no es
observable, que es exactamente lo que el resto del Feature Store hace con
cualquier dato que no existe. Un consumidor que ya sabe tratar un ``atr``
ausente sabe tratar un ``queue_imbalance`` ausente.
"""

from typing import Any

from app.engine.feature_store import FeatureStore
from app.engine.microstructure.engine import MicrostructureEngine

# Cada feature escalar es un campo del snapshot. Se declaran aquí, en un solo
# sitio, para que añadir una métrica al motor no exija tocar el cableado.
_SCALARS: tuple[str, ...] = (
    "queue_imbalance",
    "queue_ahead",
    "order_arrival_rate",
    "cancel_rate",
    "book_resiliency",
    "replenishment",
    "liquidity_consumption",
    "market_impact_bps",
    "execution_pressure",
)


def register_microstructure_features(store: FeatureStore, engine: MicrostructureEngine) -> None:
    """Expose the microstructure metrics as Feature Store entries.

    Args:
        store: Feature Store donde registrarlas.
        engine: Motor que las calcula.
    """

    def _scalar(field: str):  # type: ignore[no-untyped-def]
        async def provider(symbol: str, params: dict[str, Any]) -> float | None:
            snapshot = engine.snapshot(symbol)
            if not snapshot.observable:
                return None
            value = getattr(snapshot, field)
            return None if value is None else float(value)

        return provider

    for field in _SCALARS:
        store.register(field, _scalar(field))

    async def _object(symbol: str, params: dict[str, Any]) -> object:
        """El snapshot entero, incluido el motivo de no ser observable."""
        return engine.snapshot(symbol)

    store.register_object("microstructure", _object)
