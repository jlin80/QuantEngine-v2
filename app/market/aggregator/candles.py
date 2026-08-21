"""Agregador de velas: transforma trades en velas de cualquier timeframe.

No depende del broker: agrega los trades ya normalizados. Una vela cierra
cuando llega un trade fuera de su bucket o cuando :meth:`flush_stale` decide
que el bucket expiró sin actividad (símbolos poco líquidos).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.market.models import Candle, Ticker, Timeframe, Trade, TradeSide


@dataclass(slots=True)
class _Building:
    """Vela en construcción (mutable, interna al agregador)."""

    symbol: str
    provider: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    notional: float = 0.0
    trades: int = 0

    def add(self, trade: Trade) -> None:
        """Fold one trade into the building candle."""
        self.add_point(trade.price, trade.size, side=trade.side, notional=trade.notional)

    def add_point(
        self,
        price: float,
        size: float,
        *,
        side: TradeSide | None = None,
        notional: float | None = None,
    ) -> None:
        """Fold one price point (trade o quote) into the building candle."""
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += size
        self.notional += notional if notional is not None else price * size
        self.trades += 1
        if side is TradeSide.BUY:
            self.buy_volume += size
        elif side is TradeSide.SELL:
            self.sell_volume += size

    def freeze(self, *, closed: bool) -> Candle:
        """Materialize an immutable Candle from the current state."""
        return Candle(
            symbol=self.symbol,
            provider=self.provider,
            timeframe=self.timeframe,
            start=self.start,
            end=self.end,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            buy_volume=self.buy_volume,
            sell_volume=self.sell_volume,
            vwap=self.notional / self.volume if self.volume > 0 else self.close,
            trades=self.trades,
            closed=closed,
            source="aggregated",
        )


class CandleAggregator:
    """Builds candles for every configured timeframe from a trade stream.

    Args:
        timeframes: Timeframes a construir (se ignora ``TICK``).
    """

    def __init__(self, timeframes: Sequence[Timeframe]) -> None:
        self._timeframes = tuple(tf for tf in timeframes if tf is not Timeframe.TICK)
        self._building: dict[tuple[str, Timeframe], _Building] = {}
        self._closed_count = 0
        # Estado de la regla del tick, por símbolo: último precio visto y último
        # lado atribuido (para los quotes que no mueven el precio).
        self._last_price: dict[str, float] = {}
        self._last_side: dict[str, TradeSide] = {}

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        """Timeframes que este agregador construye."""
        return self._timeframes

    @property
    def closed_count(self) -> int:
        """Cuántas velas ha cerrado desde el arranque."""
        return self._closed_count

    def add_trade(self, trade: Trade) -> list[Candle]:
        """Fold a trade into every timeframe; return candles that closed.

        Args:
            trade: Trade validado.

        Returns:
            Velas cerradas por este trade (usualmente vacío).
        """
        return self._fold(
            trade.symbol,
            trade.provider,
            trade.exchange_ts,
            trade.price,
            trade.size,
            side=trade.side,
            notional=trade.notional,
        )

    def add_ticker(self, ticker: Ticker) -> list[Candle]:
        """Fold a quote (bid/ask mid) into every timeframe; return closed candles.

        Fuente de velas para feeds *sin* tape de trades (p. ej. MT5): cada quote
        aporta un punto de precio (mid) y cuenta como un tick de volumen, igual
        que el ``tick_volume`` de MT5.

        **Clasificación de agresor por la regla del tick.** Antes se doblaba con
        ``side=None``, así que ``buy_volume`` y ``sell_volume`` quedaban en cero
        en todas las velas y las estrategias ``delta_confirmation`` y ``cvd``
        calculaban siempre 0 — figuraban activas y votaban en el consenso sin
        medir nada (verificado en producción el 2026-08-21). La regla del tick
        es el proxy estándar cuando no hay tape: un quote que sube respecto al
        anterior se atribuye al comprador, uno que baja al vendedor, y uno que
        no se mueve hereda el lado del anterior.

        Mide **presión de cotización, no agresión ejecutada** — el CFD de Exness
        no publica operaciones (``last`` y ``volume`` llegan a cero), así que no
        hay agresión real que medir. Es un proxy más débil que el delta de un
        tape, y conviene no leerlo como si fuera lo mismo.

        Args:
            ticker: Quote normalizado.

        Returns:
            Velas cerradas por este quote (usualmente vacío).
        """
        price = ticker.last if ticker.last else ticker.mid
        if price <= 0:
            return []
        side = self._tick_rule_side(ticker.symbol, price)
        return self._fold(ticker.symbol, ticker.provider, ticker.exchange_ts, price, 1.0, side=side)

    def _tick_rule_side(self, symbol: str, price: float) -> TradeSide | None:
        """Classify a quote as buyer- or seller-initiated (tick rule).

        Devuelve ``None`` sólo en el primer quote de un símbolo, cuando no hay
        precio previo con el que comparar: ahí no se sabe el lado, y suponerlo
        sería inventar la primera unidad de delta de cada arranque.
        """
        previous = self._last_price.get(symbol)
        self._last_price[symbol] = price
        if previous is None:
            return None
        side: TradeSide | None
        if price > previous:
            side = TradeSide.BUY
        elif price < previous:
            side = TradeSide.SELL
        else:
            # Quote sin cambio: hereda el lado anterior (regla del tick clásica).
            side = self._last_side.get(symbol)
        if side is not None:
            self._last_side[symbol] = side
        return side

    def _fold(
        self,
        symbol: str,
        provider: str,
        exchange_ts: datetime,
        price: float,
        size: float,
        *,
        side: TradeSide | None = None,
        notional: float | None = None,
    ) -> list[Candle]:
        """Fold one price point into every timeframe; return closed candles."""
        closed: list[Candle] = []
        for timeframe in self._timeframes:
            key = (symbol, timeframe)
            building = self._building.get(key)
            if building is not None and exchange_ts >= building.end:
                closed.append(building.freeze(closed=True))
                self._closed_count += 1
                building = None
            if building is None:
                start = timeframe.bucket_start(exchange_ts)
                building = _Building(
                    symbol=symbol,
                    provider=provider,
                    timeframe=timeframe,
                    start=start,
                    end=timeframe.bucket_end(start),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                )
                self._building[key] = building
            building.add_point(price, size, side=side, notional=notional)
        return closed

    def building_candle(self, symbol: str, timeframe: Timeframe) -> Candle | None:
        """Return the current partial candle (``closed=False``) if any."""
        building = self._building.get((symbol, timeframe))
        return None if building is None else building.freeze(closed=False)

    def flush_stale(self, now: datetime) -> list[Candle]:
        """Close every building candle whose bucket already ended.

        Pensado para símbolos poco líquidos: sin trades nuevos, la vela se
        cierra cuando el reloj rebasa el fin del bucket.

        Args:
            now: Reloj actual (UTC).

        Returns:
            Velas cerradas por expiración.
        """
        closed: list[Candle] = []
        for key, building in list(self._building.items()):
            if now >= building.end:
                closed.append(building.freeze(closed=True))
                self._closed_count += 1
                del self._building[key]
        return closed
