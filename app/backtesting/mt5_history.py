"""Lectura de histórico 1m desde un terminal MetaTrader 5.

Compartido por el runner CLI (``scripts/backtest_quantcore.py``) y el endpoint
del dashboard: convierte ``copy_rates_from_pos`` en :class:`Candle` internas.
Nunca inicializa ni cierra el terminal cuando recibe un módulo ya conectado
(caso del motor en vivo, que comparte la conexión con el feed y el broker).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any

from app.market.models import Candle, Timeframe


def pull_candles(
    mt5: ModuleType,
    symbol: str,
    bars: int,
    *,
    resolve: Callable[[str], str] | None = None,
) -> list[Candle]:
    """Fetch ``bars`` closed 1m candles for ``symbol`` from an MT5 terminal.

    Args:
        mt5: Módulo ``MetaTrader5`` ya inicializado (conexión viva).
        symbol: Símbolo interno (p. ej. ``ETHUSDM``).
        bars: Número de velas 1m a leer (las más recientes).
        resolve: Resolutor opcional del nombre real en el terminal (caja).

    Returns:
        Velas en orden cronológico (más antigua primero).

    Raises:
        RuntimeError: Si el símbolo no existe o ``copy_rates`` no devuelve datos.
    """
    real = resolve(symbol) if resolve is not None else symbol
    if mt5.symbol_info(real) is None:
        raise RuntimeError(f"símbolo desconocido en MT5: {symbol} (resuelto: {real})")
    rates = mt5.copy_rates_from_pos(real, mt5.TIMEFRAME_M1, 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"copy_rates_from_pos sin datos para {real}: {mt5.last_error()}")
    return [_row_to_candle(symbol.upper(), row) for row in rates]


def _row_to_candle(symbol: str, row: Any) -> Candle:
    """Convert one MT5 rate row into an internal :class:`Candle`."""
    start = datetime.fromtimestamp(int(row["time"]), tz=UTC)
    volume = float(row["tick_volume"])
    return Candle(
        symbol=symbol,
        provider="mt5_history",
        timeframe=Timeframe.M1,
        start=start,
        end=start + timedelta(minutes=1),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=volume,
        trades=int(volume),
        closed=True,
        source="provider",
    )
