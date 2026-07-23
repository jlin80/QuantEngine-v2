"""Order Flow: delta, CVD, agresión, absorción y agotamiento.

Incluye heurísticas EXPERIMENTALES de spoofing/iceberg/consumo sobre trades
recientes + libro actual. Limitación documentada: sin historial de libro
nivel-a-nivel, esas detecciones son aproximadas.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.market.models import OrderBook, Trade, TradeSide


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderFlowSnapshot:
    """Lectura compuesta del flujo de órdenes de una ventana de trades.

    Attributes:
        trades: Trades considerados.
        buy_volume / sell_volume: Volumen agresor por lado.
        delta: ``buy - sell``.
        cvd_series: Delta acumulado por bucket temporal.
        cvd_slope: Pendiente del CVD (delta del último tercio vs primero,
            normalizada por el volumen total; rango aprox. [-1, 1]).
        aggression_ratio: ``buy / (buy + sell)`` (0.5 = equilibrado).
        avg_trade_size: Tamaño medio.
        large_trade_ratio: Fracción del volumen hecha por trades grandes
            (> percentil configurado).
        price_change_pct: Variación del precio primera→última operación.
        absorption: ``"bullish"``/``"bearish"``/"" — delta fuerte SIN
            desplazamiento a favor (alguien absorbe la agresión).
        exhaustion: ``"buyers"``/``"sellers"``/"" — la agresión dominante se
            apaga bucket a bucket.
        imbalance: Imbalance del libro actual (si hay libro).
        book_pressure: Presión ponderada del libro actual (si hay libro).
        spoofing_score: [0-1] EXPERIMENTAL — liquidez desproporcionada lejos
            del mid vs cerca (posible papel falso).
        iceberg_score: [0-1] EXPERIMENTAL — volumen ejecutado en un mismo
            precio muy superior al tamaño visible del libro.
        liquidity_consumption: Volumen operado / profundidad visible top-5
            (>1 = se consumió más de lo mostrado). EXPERIMENTAL.
    """

    trades: int
    buy_volume: float
    sell_volume: float
    delta: float
    cvd_series: tuple[float, ...]
    cvd_slope: float
    aggression_ratio: float
    avg_trade_size: float
    large_trade_ratio: float
    price_change_pct: float
    absorption: str
    exhaustion: str
    imbalance: float | None
    book_pressure: float | None
    spoofing_score: float
    iceberg_score: float
    liquidity_consumption: float | None


def analyze_order_flow(
    trades: Sequence[Trade],
    book: OrderBook | None = None,
    *,
    buckets: int = 6,
    absorption_move_pct: float = 0.05,
    absorption_min_ratio: float = 0.65,
    large_trade_percentile: float = 0.9,
    iceberg_multiple: float = 3.0,
    spoof_far_ratio: float = 3.0,
) -> OrderFlowSnapshot | None:
    """Analyze recent aggressive flow (and the current book if available).

    Args:
        trades: Trades recientes en orden cronológico (mínimo ``buckets``).
        book: Snapshot actual del libro (opcional).
        buckets: Segmentos temporales para CVD/exhaustion.
        absorption_move_pct: Movimiento máximo (%) para hablar de absorción.
        absorption_min_ratio: Agresión mínima dominante para absorción.
        large_trade_percentile: Umbral de "trade grande".
        iceberg_multiple: Ejecutado/visible mínimo para candidato iceberg.
        spoof_far_ratio: Liquidez lejana/cercana mínima para spoofing score.

    Returns:
        Lectura completa o ``None`` con menos trades que buckets.
    """
    if len(trades) < buckets:
        return None
    buy = sum(t.size for t in trades if t.side is TradeSide.BUY)
    sell = sum(t.size for t in trades if t.side is TradeSide.SELL)
    total = buy + sell
    delta = buy - sell

    # CVD por buckets temporales de igual número de trades (determinista).
    per_bucket = len(trades) // buckets
    cvd: list[float] = []
    acc = 0.0
    bucket_deltas: list[float] = []
    for b in range(buckets):
        chunk = trades[b * per_bucket : (b + 1) * per_bucket if b < buckets - 1 else len(trades)]
        d = sum(
            t.size if t.side is TradeSide.BUY else -t.size if t.side is TradeSide.SELL else 0.0
            for t in chunk
        )
        bucket_deltas.append(d)
        acc += d
        cvd.append(acc)
    third = max(1, buckets // 3)
    slope_raw = sum(bucket_deltas[-third:]) - sum(bucket_deltas[:third])
    cvd_slope = max(-1.0, min(1.0, slope_raw / total)) if total > 0 else 0.0

    sizes = sorted(t.size for t in trades)
    avg_size = sum(sizes) / len(sizes)
    threshold = sizes[min(len(sizes) - 1, int(large_trade_percentile * len(sizes)))]
    large_volume = sum(t.size for t in trades if t.size >= threshold)
    volume_all = sum(t.size for t in trades)
    large_ratio = large_volume / volume_all if volume_all > 0 else 0.0

    first_price, last_price = trades[0].price, trades[-1].price
    price_change = (last_price / first_price - 1.0) * 100.0 if first_price > 0 else 0.0

    aggression = buy / total if total > 0 else 0.5
    absorption = ""
    if abs(price_change) <= absorption_move_pct:
        if aggression >= absorption_min_ratio:
            absorption = "bearish"  # compran agresivo y el precio no sube: oferta absorbe
        elif aggression <= 1.0 - absorption_min_ratio:
            absorption = "bullish"

    exhaustion = ""
    if len(bucket_deltas) >= 3:
        d1, d2, d3 = bucket_deltas[-3], bucket_deltas[-2], bucket_deltas[-1]
        if d1 > 0 and d1 > d2 > d3:
            exhaustion = "buyers"
        elif d1 < 0 and d1 < d2 < d3:
            exhaustion = "sellers"

    imbalance: float | None = None
    pressure: float | None = None
    spoof = 0.0
    iceberg = 0.0
    consumption: float | None = None
    if book is not None:
        imbalance = book.imbalance()
        pressure = book.book_pressure()
        spoof = _spoofing_score(book, far_ratio=spoof_far_ratio)
        iceberg = _iceberg_score(trades, book, multiple=iceberg_multiple)
        bid_depth, ask_depth = book.depth(5)
        visible = bid_depth + ask_depth
        consumption = volume_all / visible if visible > 0 else None

    return OrderFlowSnapshot(
        trades=len(trades),
        buy_volume=buy,
        sell_volume=sell,
        delta=delta,
        cvd_series=tuple(cvd),
        cvd_slope=cvd_slope,
        aggression_ratio=aggression,
        avg_trade_size=avg_size,
        large_trade_ratio=large_ratio,
        price_change_pct=price_change,
        absorption=absorption,
        exhaustion=exhaustion,
        imbalance=imbalance,
        book_pressure=pressure,
        spoofing_score=spoof,
        iceberg_score=iceberg,
        liquidity_consumption=consumption,
    )


def _spoofing_score(book: OrderBook, *, far_ratio: float) -> float:
    """EXPERIMENTAL: liquidez desproporcionada lejos del mid vs pegada a él."""
    near_bid, near_ask = book.depth(3)
    total_bid, total_ask = book.depth(15)
    far_bid = total_bid - near_bid
    far_ask = total_ask - near_ask
    near = near_bid + near_ask
    far = far_bid + far_ask
    if near <= 0:
        return 0.0
    ratio = far / near
    if ratio <= far_ratio:
        return 0.0
    return min(1.0, (ratio - far_ratio) / far_ratio)


def _iceberg_score(trades: Sequence[Trade], book: OrderBook, *, multiple: float) -> float:
    """EXPERIMENTAL: volumen ejecutado en un precio ≫ tamaño visible del top."""
    top_bid, top_ask = book.best_bid, book.best_ask
    visible = 0.0
    if top_bid is not None:
        visible += top_bid.size
    if top_ask is not None:
        visible += top_ask.size
    if visible <= 0:
        return 0.0
    by_price: dict[float, float] = {}
    for trade in trades:
        by_price[trade.price] = by_price.get(trade.price, 0.0) + trade.size
    heaviest = max(by_price.values(), default=0.0)
    ratio = heaviest / visible
    if ratio <= multiple:
        return 0.0
    return min(1.0, (ratio - multiple) / multiple)
