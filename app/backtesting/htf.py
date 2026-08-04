"""Agregación de velas 1m a marcos superiores, sin lookahead.

El motor es hoy estrictamente monotimeframe: las 20 estrategias, el detector de
régimen, el Market Context y el Feature Store leen todos velas de 1m. Con el
`lookback` de 50 del detector, **toda la visión de mercado del motor son 50
minutos** — no hay sesgo de 1h, ni dirección de 15m, ni confluencia entre marcos.

Este módulo permite alimentar marcos superiores en el backtest a partir de la
misma serie de 1m, para poder **medir** si ese contexto aporta algo antes de
cablearlo en producción.

**La regla que gobierna el módulo es no mirar al futuro.** Una vela de 1h sólo
se emite cuando está completamente cerrada — es decir, cuando llega una vela de
1m que pertenece al bucket siguiente. Emitir la vela en curso pondría en manos
de la estrategia el máximo y el mínimo de minutos que todavía no han ocurrido, y
un backtest con lookahead no es un backtest optimista: es uno inválido.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.market.models import Candle, Timeframe


def bucket_start(moment: datetime, seconds: int) -> datetime:
    """Inicio del bucket de ``seconds`` al que pertenece ``moment``.

    Args:
        moment: Instante a alinear (UTC).
        seconds: Duración del bucket en segundos.

    Returns:
        El inicio del bucket, alineado al epoch para que sea estable entre
        corridas y coincida con la alineación de los exchanges.
    """
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=UTC)


@dataclass(slots=True)
class _Bucket:
    """Vela superior en construcción."""

    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    trades: int = 0
    buy_volume: float = 0.0
    sell_volume: float = 0.0


@dataclass(slots=True)
class HigherTimeframeAggregator:
    """Roll 1m candles into one higher timeframe, emitting only closed candles.

    Args:
        timeframe: Marco de destino (debe tener duración fija).
        symbol: Símbolo de las velas emitidas.
        provider: Proveedor declarado en las velas emitidas.
    """

    timeframe: Timeframe
    symbol: str
    provider: str = "backtest-htf"
    _bucket: _Bucket | None = field(default=None, init=False)

    def push(self, candle: Candle) -> Candle | None:
        """Feed one 1m candle; return a higher-timeframe candle when one closes.

        Args:
            candle: Vela de 1m cerrada.

        Returns:
            La vela superior **cerrada** si esta vela abrió un bucket nuevo, o
            ``None`` si el bucket en curso sigue abierto.

        Raises:
            ValueError: Si el timeframe de destino no tiene duración fija.
        """
        seconds = self.timeframe.seconds
        if seconds is None:
            raise ValueError(f"{self.timeframe} no tiene duración fija")
        start = bucket_start(candle.start, seconds)

        if self._bucket is None:
            self._bucket = _Bucket(
                start=start,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                trades=candle.trades,
                buy_volume=candle.buy_volume,
                sell_volume=candle.sell_volume,
            )
            return None

        if start == self._bucket.start:
            # Mismo bucket: acumular y NO emitir. La vela en curso no puede
            # verse todavía: contiene minutos que la estrategia aún no vivió.
            self._bucket.high = max(self._bucket.high, candle.high)
            self._bucket.low = min(self._bucket.low, candle.low)
            self._bucket.close = candle.close
            self._bucket.volume += candle.volume
            self._bucket.trades += candle.trades
            self._bucket.buy_volume += candle.buy_volume
            self._bucket.sell_volume += candle.sell_volume
            return None

        # Bucket nuevo: el anterior ya está cerrado y es seguro publicarlo.
        closed = self._emit(self._bucket, seconds)
        self._bucket = _Bucket(
            start=start,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            trades=candle.trades,
            buy_volume=candle.buy_volume,
            sell_volume=candle.sell_volume,
        )
        return closed

    def _emit(self, bucket: _Bucket, seconds: int) -> Candle:
        """Build the immutable candle for a finished bucket."""
        return Candle(
            symbol=self.symbol,
            provider=self.provider,
            timeframe=self.timeframe,
            start=bucket.start,
            end=bucket.start + timedelta(seconds=seconds),
            open=bucket.open,
            high=bucket.high,
            low=bucket.low,
            close=bucket.close,
            volume=bucket.volume,
            trades=bucket.trades,
            buy_volume=bucket.buy_volume,
            sell_volume=bucket.sell_volume,
            closed=True,
            source="aggregator",
        )

    def reset(self) -> None:
        """Forget the bucket in progress (nueva corrida)."""
        self._bucket = None
