"""Construcción de niveles de operación (entry / stop / target)."""

from app.engine.models import Direction, EntryZone


def entry_zone_around(price: float, atr: float, *, width_atr: float = 0.15) -> EntryZone:
    """Banda de entrada centrada en un precio, con ancho en ATRs.

    Args:
        price: Precio central de la entrada propuesta.
        atr: ATR de referencia.
        width_atr: Semiancho de la banda en múltiplos de ATR.

    Returns:
        Zona de entrada simétrica.
    """
    half = max(0.0, atr * width_atr)
    return EntryZone(low=price - half, high=price + half)


def protective_stop(
    direction: Direction, reference: float, atr: float, *, buffer_atr: float = 0.5
) -> float:
    """Stop protectivo más allá de un nivel de referencia.

    Args:
        direction: Dirección de la operación.
        reference: Nivel a proteger (extremo de la estructura/zona).
        atr: ATR de referencia.
        buffer_atr: Colchón en ATRs más allá del nivel.

    Returns:
        Precio del stop (debajo para longs, encima para shorts).
    """
    pad = max(0.0, atr * buffer_atr)
    return reference - pad if direction is Direction.LONG else reference + pad


def target_from_rr(entry: float, stop: float, *, risk_reward: float = 2.0) -> float:
    """Take profit por múltiplo del riesgo asumido.

    Args:
        entry: Precio de entrada.
        stop: Stop loss.
        risk_reward: Múltiplo R objetivo.

    Returns:
        Precio objetivo en la dirección opuesta al stop.
    """
    risk = entry - stop
    return entry + risk * risk_reward
