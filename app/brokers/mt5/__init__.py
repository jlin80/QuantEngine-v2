"""Adaptador MetaTrader 5 (Exness demo) para ejecución y datos.

Este paquete es la primera implementación de un broker **real** en v2. Enruta
órdenes a una cuenta **demo** de Exness a través del terminal MetaTrader 5 y su
paquete Python oficial (``MetaTrader5``), que sólo funciona en Windows con el
terminal instalado y logueado.

Regla de seguridad: este adaptador se selecciona únicamente en modo ``demo``
(ver :class:`~app.execution.models.enums.ExecutionMode`). El modo ``live``
(dinero real) sigue prohibido por el Live Gate — este código nunca lo habilita.
"""

from app.brokers.mt5.broker import MT5Broker
from app.brokers.mt5.connection import MT5Connection, MT5ConnectionConfig

__all__ = ["MT5Broker", "MT5Connection", "MT5ConnectionConfig"]
