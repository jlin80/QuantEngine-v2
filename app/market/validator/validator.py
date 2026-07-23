"""Validación de calidad de datos antes de publicar nada al sistema.

Todo objeto normalizado pasa por aquí. Los datos corruptos se descartan y se
contabilizan; las anomalías tolerables se marcan sin descartar. El validador
es puro (sin I/O): devuelve issues y el pipeline decide publicarlas.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.market.models import (
    Candle,
    FundingRate,
    Liquidation,
    OpenInterest,
    OrderBookDelta,
    QualityIssueType,
    Ticker,
    Trade,
)


@dataclass(frozen=True, slots=True)
class QualityIssue:
    """Un problema de calidad detectado en un dato.

    Attributes:
        issue: Tipo de problema.
        symbol: Símbolo afectado.
        detail: Descripción corta con los valores implicados.
        discard: Si el dato debe descartarse (``False`` = solo marcar).
    """

    issue: QualityIssueType
    symbol: str
    detail: str
    discard: bool


@dataclass(slots=True)
class _SymbolTrack:
    """Estado por símbolo para detectar duplicados y desorden."""

    last_trade_ts: datetime | None = None
    last_price: float | None = None
    recent_ids: deque[str] | None = None


class DataValidator:
    """Stateful data-quality gate for normalized market objects.

    Args:
        max_price_jump_pct: Salto máximo tolerado entre trades consecutivos.
        max_volume: Volumen máximo plausible por trade/vela.
        future_tolerance_seconds: Cuánto futuro se tolera en ``exchange_ts``.
        stale_seconds: Antigüedad a partir de la cual un dato se marca stale.
        duplicate_window: Cuántos trade IDs recientes recordar por símbolo.
        out_of_order_grace: Retroceso tolerado (segundos) sin descartar.
    """

    def __init__(
        self,
        *,
        max_price_jump_pct: float = 20.0,
        max_volume: float = 1e12,
        future_tolerance_seconds: float = 5.0,
        stale_seconds: float = 120.0,
        duplicate_window: int = 4096,
        out_of_order_grace: float = 2.0,
    ) -> None:
        self._max_jump = max_price_jump_pct
        self._max_volume = max_volume
        self._future_tolerance = timedelta(seconds=future_tolerance_seconds)
        self._stale = timedelta(seconds=stale_seconds)
        self._dup_window = duplicate_window
        self._ooo_grace = timedelta(seconds=out_of_order_grace)
        self._tracks: dict[str, _SymbolTrack] = {}
        self._counters: dict[str, int] = {}
        self._checked = 0
        self._discarded = 0
        self._log = logging.getLogger("app.market.validator")

    # ------------------------------------------------------------------
    # Entradas por tipo
    # ------------------------------------------------------------------

    def validate_trade(self, trade: Trade) -> list[QualityIssue]:
        """Validate a trade; empty list means clean.

        Args:
            trade: Trade normalizado.

        Returns:
            Issues detectadas (con su decisión de descarte).
        """
        issues = self._common_checks(
            symbol=trade.symbol,
            price=trade.price,
            exchange_ts=trade.exchange_ts,
            local_ts=trade.local_ts,
        )
        if trade.size < 0:
            issues.append(
                self._issue(
                    QualityIssueType.NEGATIVE_SIZE, trade.symbol, f"size={trade.size}", discard=True
                )
            )
        elif trade.size > self._max_volume:
            issues.append(
                self._issue(
                    QualityIssueType.IMPOSSIBLE_VOLUME,
                    trade.symbol,
                    f"size={trade.size} > max={self._max_volume}",
                    discard=True,
                )
            )

        track = self._track(trade.symbol)
        if trade.trade_id:
            if track.recent_ids is None:
                track.recent_ids = deque(maxlen=self._dup_window)
            if trade.trade_id in track.recent_ids:
                issues.append(
                    self._issue(
                        QualityIssueType.DUPLICATE,
                        trade.symbol,
                        f"trade_id={trade.trade_id}",
                        discard=True,
                    )
                )
            else:
                track.recent_ids.append(trade.trade_id)

        if (
            track.last_trade_ts is not None
            and trade.exchange_ts < track.last_trade_ts - self._ooo_grace
        ):
            issues.append(
                self._issue(
                    QualityIssueType.OUT_OF_ORDER,
                    trade.symbol,
                    f"ts={trade.exchange_ts.isoformat()} < last={track.last_trade_ts.isoformat()}",
                    discard=True,
                )
            )

        if (
            track.last_price is not None
            and track.last_price > 0
            and trade.price > 0
            and abs(trade.price - track.last_price) / track.last_price * 100.0 > self._max_jump
        ):
            issues.append(
                self._issue(
                    QualityIssueType.PRICE_GAP,
                    trade.symbol,
                    f"price={trade.price} vs last={track.last_price} (> {self._max_jump}%)",
                    discard=True,
                )
            )

        if not self._should_discard(issues):
            track.last_trade_ts = max(track.last_trade_ts or trade.exchange_ts, trade.exchange_ts)
            track.last_price = trade.price
        return self._account(issues)

    def validate_ticker(self, ticker: Ticker) -> list[QualityIssue]:
        """Validate a best bid/ask update."""
        issues = self._common_checks(
            symbol=ticker.symbol,
            price=ticker.bid,
            exchange_ts=ticker.exchange_ts,
            local_ts=ticker.local_ts,
        )
        if ticker.ask <= 0:
            issues.append(
                self._issue(
                    QualityIssueType.NEGATIVE_PRICE,
                    ticker.symbol,
                    f"ask={ticker.ask}",
                    discard=True,
                )
            )
        elif ticker.bid > 0 and ticker.bid > ticker.ask:
            issues.append(
                self._issue(
                    QualityIssueType.CROSSED_BOOK,
                    ticker.symbol,
                    f"bid={ticker.bid} > ask={ticker.ask}",
                    discard=True,
                )
            )
        return self._account(issues)

    def validate_candle(self, candle: Candle) -> list[QualityIssue]:
        """Validate candle integrity (OHLC coherente, volumen, rango temporal)."""
        issues: list[QualityIssue] = []
        body_high, body_low = max(candle.open, candle.close), min(candle.open, candle.close)
        if candle.high < body_high or candle.low > body_low or candle.low > candle.high:
            issues.append(
                self._issue(
                    QualityIssueType.MALFORMED_CANDLE,
                    candle.symbol,
                    f"ohlc=({candle.open},{candle.high},{candle.low},{candle.close})",
                    discard=True,
                )
            )
        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            issues.append(
                self._issue(
                    QualityIssueType.NEGATIVE_PRICE,
                    candle.symbol,
                    "precio <= 0 en OHLC",
                    discard=True,
                )
            )
        if candle.volume < 0 or candle.volume > self._max_volume:
            issues.append(
                self._issue(
                    QualityIssueType.IMPOSSIBLE_VOLUME,
                    candle.symbol,
                    f"volume={candle.volume}",
                    discard=True,
                )
            )
        if candle.end <= candle.start:
            issues.append(
                self._issue(
                    QualityIssueType.INVALID_TIMESTAMP, candle.symbol, "end <= start", discard=True
                )
            )
        return self._account(issues)

    def validate_book_delta(self, delta: OrderBookDelta) -> list[QualityIssue]:
        """Validate an order-book snapshot/delta (precios y tamaños)."""
        issues: list[QualityIssue] = []
        for level in (*delta.bids, *delta.asks):
            if level.price <= 0:
                issues.append(
                    self._issue(
                        QualityIssueType.NEGATIVE_PRICE,
                        delta.symbol,
                        f"level price={level.price}",
                        discard=True,
                    )
                )
                break
            if level.size < 0:
                issues.append(
                    self._issue(
                        QualityIssueType.NEGATIVE_SIZE,
                        delta.symbol,
                        f"level size={level.size}",
                        discard=True,
                    )
                )
                break
        return self._account(issues)

    def validate_funding(self, funding: FundingRate) -> list[QualityIssue]:
        """Validate a funding-rate update (timestamps)."""
        issues = self._timestamp_checks(funding.symbol, funding.exchange_ts, funding.local_ts)
        return self._account(issues)

    def validate_open_interest(self, oi: OpenInterest) -> list[QualityIssue]:
        """Validate an open-interest update."""
        issues = self._timestamp_checks(oi.symbol, oi.exchange_ts, oi.local_ts)
        if oi.contracts < 0:
            issues.append(
                self._issue(
                    QualityIssueType.NEGATIVE_SIZE,
                    oi.symbol,
                    f"contracts={oi.contracts}",
                    discard=True,
                )
            )
        return self._account(issues)

    def validate_liquidation(self, liq: Liquidation) -> list[QualityIssue]:
        """Validate a liquidation report."""
        issues = self._common_checks(
            symbol=liq.symbol, price=liq.price, exchange_ts=liq.exchange_ts, local_ts=liq.local_ts
        )
        if liq.size < 0:
            issues.append(
                self._issue(
                    QualityIssueType.NEGATIVE_SIZE, liq.symbol, f"size={liq.size}", discard=True
                )
            )
        return self._account(issues)

    # ------------------------------------------------------------------
    # Reglas compartidas
    # ------------------------------------------------------------------

    def _common_checks(
        self, *, symbol: str, price: float, exchange_ts: datetime, local_ts: datetime
    ) -> list[QualityIssue]:
        """Price + timestamp checks shared by most objects."""
        issues = self._timestamp_checks(symbol, exchange_ts, local_ts)
        if price <= 0:
            issues.append(
                self._issue(QualityIssueType.NEGATIVE_PRICE, symbol, f"price={price}", discard=True)
            )
        return issues

    def _timestamp_checks(
        self, symbol: str, exchange_ts: datetime, local_ts: datetime
    ) -> list[QualityIssue]:
        """Detect naive, future or stale exchange timestamps."""
        issues: list[QualityIssue] = []
        if exchange_ts.tzinfo is None or local_ts.tzinfo is None:
            issues.append(
                self._issue(
                    QualityIssueType.INVALID_TIMESTAMP,
                    symbol,
                    "timestamp sin zona horaria",
                    discard=True,
                )
            )
            return issues
        if exchange_ts > local_ts + self._future_tolerance:
            issues.append(
                self._issue(
                    QualityIssueType.FUTURE_TIMESTAMP,
                    symbol,
                    f"exchange_ts={exchange_ts.isoformat()} > local+{self._future_tolerance}",
                    discard=True,
                )
            )
        elif local_ts - exchange_ts > self._stale:
            # Dato viejo: se marca pero no se descarta (backfill legítimo).
            issues.append(
                self._issue(
                    QualityIssueType.STALE_TIMESTAMP,
                    symbol,
                    f"age={(local_ts - exchange_ts).total_seconds():.1f}s",
                    discard=False,
                )
            )
        return issues

    # ------------------------------------------------------------------
    # Utilidades / métricas
    # ------------------------------------------------------------------

    @staticmethod
    def _should_discard(issues: list[QualityIssue]) -> bool:
        """Whether any issue demands discarding the object."""
        return any(issue.discard for issue in issues)

    def should_discard(self, issues: list[QualityIssue]) -> bool:
        """Public form of :meth:`_should_discard` for the pipeline."""
        return self._should_discard(issues)

    def _issue(
        self, issue: QualityIssueType, symbol: str, detail: str, *, discard: bool
    ) -> QualityIssue:
        """Build a QualityIssue."""
        return QualityIssue(issue=issue, symbol=symbol, detail=detail, discard=discard)

    def _track(self, symbol: str) -> _SymbolTrack:
        """Get (or create) the per-symbol tracking state."""
        track = self._tracks.get(symbol)
        if track is None:
            track = _SymbolTrack()
            self._tracks[symbol] = track
        return track

    def _account(self, issues: list[QualityIssue]) -> list[QualityIssue]:
        """Update counters and log every issue."""
        self._checked += 1
        if self._should_discard(issues):
            self._discarded += 1
        for issue in issues:
            self._counters[issue.issue.value] = self._counters.get(issue.issue.value, 0) + 1
            self._log.warning(
                "Data quality [%s] %s: %s (discard=%s)",
                issue.symbol,
                issue.issue.value,
                issue.detail,
                issue.discard,
            )
        return issues

    @property
    def stats(self) -> dict[str, int]:
        """Counters: objetos revisados, descartados y por tipo de issue."""
        return {"checked": self._checked, "discarded": self._discarded, **self._counters}
