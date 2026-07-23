"""Selección del broker de ejecución según el modo resuelto.

Hasta la Fase 8 el composition root construía un ``PaperBroker`` directamente,
sin condicional alguno: la "prohibición" de live era que nadie había escrito el
código para hacerlo. Ahora hay una decisión explícita y auditable.

Salidas posibles:

* ``paper`` → simulador ``PaperBroker`` (por defecto).
* ``demo``  → ``MT5Broker`` real contra una cuenta **demo** (requiere una
  ``MT5Connection`` ya provista por el composition root).
* ``live``  → **prohibido**: no existe adaptador de dinero real y esta función
  falla ruidosamente en vez de degradar en silencio a paper.
"""

import logging

from app.brokers.mt5.connection import MT5Connection
from app.config.settings import ExecutionSettings
from app.core.exceptions import ConfigurationError
from app.core.interfaces.broker import ExecutionBroker
from app.execution.commission import CommissionEngine
from app.execution.latency import LatencyEngine
from app.execution.paper_engine import PaperBroker
from app.execution.slippage import SlippageEngine

_log = logging.getLogger("app.production.live.broker")


def select_broker(
    mode: str,
    settings: ExecutionSettings,
    commission: CommissionEngine,
    slippage: SlippageEngine,
    latency: LatencyEngine,
    *,
    mt5_connection: MT5Connection | None = None,
    deviation_points: int = 20,
    magic: int = 777_001,
) -> ExecutionBroker:
    """Build the broker for the resolved execution mode.

    Args:
        mode: Mode resolved by :meth:`ExecutionSettings.resolved_mode` (o por el
            :class:`~app.production.live.mode.ModeResolver`).
        settings: Execution configuration.
        commission: Commission engine.
        slippage: Slippage engine.
        latency: Latency engine.
        mt5_connection: Conexión MT5 compartida (obligatoria en modo ``demo``).
        deviation_points: Desviación de precio tolerada por el broker MT5.
        magic: Identificador ``magic`` de las órdenes del motor.

    Returns:
        El broker a usar para ejecutar.

    Raises:
        ConfigurationError: Si se pide ``demo`` sin conexión MT5, o ``live``
            (no existe adaptador de dinero real).
    """
    if mode == "paper":
        return PaperBroker(settings, commission, slippage, latency)
    if mode == "demo":
        if mt5_connection is None:
            raise ConfigurationError(
                "Modo 'demo' requiere una MT5Connection; configura QE_BROKER__NAME="
                "mt5_exness y las credenciales QE_BROKER__MT5__* de la cuenta demo.",
                context={"mode": mode},
            )
        # Import perezoso: evita cargar el adaptador MT5 salvo en modo demo.
        from app.brokers.mt5.broker import MT5Broker

        _log.info("Broker de ejecución: MT5 (cuenta DEMO)")
        return MT5Broker(
            mt5_connection, deviation_points=deviation_points, magic=magic
        )
    raise ConfigurationError(
        "No existe adaptador de broker de dinero real: 'live' sigue prohibido. El "
        "motor no arranca en modo live.",
        context={"mode": mode},
    )
