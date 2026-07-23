"""Catálogo de bloques de señal y filtros de contexto (Fase 10).

Cada bloque es una regla cuantitativa comprobable con un dominio de parámetros
declarado (para el parameter lab y los optimizadores). El generador combina
bloques y filtros; el compilador evalúa sus predicados por barra. No hay código
generado dinámicamente: sólo composición de primitivas auditadas.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.engine.models import Direction
from app.market.models import Candle
from app.research import indicators as ind

# Un predicado de bloque decide la dirección para la vela ``index`` usando
# únicamente ``candles[: index + 1]`` (nunca mira el futuro).
BlockPredicate = Callable[[dict[str, float], Sequence[Candle], int], Direction]
# Un filtro permite (o veta) operar en la vela ``index``.
FilterPredicate = Callable[[dict[str, float], Sequence[Candle], int], bool]

# Rango de un parámetro: (low, high, is_int).
Range = tuple[float, float, bool]


@dataclass(frozen=True, kw_only=True, slots=True)
class BlockSpec:
    """Definición de un bloque de señal reutilizable.

    Attributes:
        kind: Identificador del bloque.
        category: Familia cuantitativa (trend/reversion/breakout/momentum/orderflow).
        defaults: Parámetros por defecto.
        ranges: Dominio optimizable por parámetro.
        needs_orderflow: Si requiere ``buy_volume``/``sell_volume`` reales.
        predicate: Función que decide la dirección por barra.
    """

    kind: str
    category: str
    defaults: dict[str, float]
    ranges: dict[str, Range]
    predicate: BlockPredicate
    needs_orderflow: bool = False


@dataclass(frozen=True, kw_only=True, slots=True)
class FilterSpec:
    """Definición de un filtro de contexto.

    Attributes:
        kind: Identificador del filtro.
        category: Familia (session/volatility/liquidity/regime).
        defaults: Parámetros por defecto.
        ranges: Dominio optimizable por parámetro.
        predicate: Función que permite o veta la operación por barra.
    """

    kind: str
    category: str
    defaults: dict[str, float]
    ranges: dict[str, Range] = field(default_factory=dict)
    predicate: FilterPredicate


def _closes(candles: Sequence[Candle], index: int) -> list[float]:
    """Closes up to and including ``index``."""
    return [c.close for c in candles[: index + 1]]


# ---------------------------------------------------------------------------
# Predicados de bloque
# ---------------------------------------------------------------------------


def _ema_cross(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    closes = _closes(candles, index)
    fast = ind.ema(closes, int(params["fast"]))
    slow = ind.ema(closes, int(params["slow"]))
    if fast is None or slow is None:
        return Direction.NEUTRAL
    return Direction.LONG if fast > slow else Direction.SHORT


def _sma_cross(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    closes = _closes(candles, index)
    fast = ind.sma(closes, int(params["fast"]))
    slow = ind.sma(closes, int(params["slow"]))
    if fast is None or slow is None:
        return Direction.NEUTRAL
    return Direction.LONG if fast > slow else Direction.SHORT


def _donchian_breakout(
    params: dict[str, float], candles: Sequence[Candle], index: int
) -> Direction:
    window = candles[: index + 1]
    high = ind.donchian_high(window, int(params["period"]))
    low = ind.donchian_low(window, int(params["period"]))
    if high is None or low is None:
        return Direction.NEUTRAL
    close = window[-1].close
    if close > high:
        return Direction.LONG
    if close < low:
        return Direction.SHORT
    return Direction.NEUTRAL


def _atr_breakout(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    window = candles[: index + 1]
    atr = ind.atr(window, int(params["period"]))
    if atr is None or len(window) < 2:
        return Direction.NEUTRAL
    prev_close = window[-2].close
    close = window[-1].close
    mult = params["mult"]
    if close > prev_close + mult * atr:
        return Direction.LONG
    if close < prev_close - mult * atr:
        return Direction.SHORT
    return Direction.NEUTRAL


def _momentum(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    closes = _closes(candles, index)
    value = ind.momentum(closes, int(params["period"]))
    if value is None:
        return Direction.NEUTRAL
    threshold = params["threshold"]
    if value > threshold:
        return Direction.LONG
    if value < -threshold:
        return Direction.SHORT
    return Direction.NEUTRAL


def _rsi_reversion(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    closes = _closes(candles, index)
    value = ind.rsi(closes, int(params["period"]))
    if value is None:
        return Direction.NEUTRAL
    if value < params["low"]:
        return Direction.LONG
    if value > params["high"]:
        return Direction.SHORT
    return Direction.NEUTRAL


def _vwap_reversion(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    window = candles[: index + 1]
    vwap = ind.rolling_vwap(window, int(params["period"]))
    if vwap is None or vwap <= 0.0:
        return Direction.NEUTRAL
    close = window[-1].close
    band = params["band"]
    if close < vwap * (1.0 - band):
        return Direction.LONG
    if close > vwap * (1.0 + band):
        return Direction.SHORT
    return Direction.NEUTRAL


def _macd(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    closes = _closes(candles, index)
    result = ind.macd(closes, int(params["fast"]), int(params["slow"]), int(params["signal"]))
    if result is None:
        return Direction.NEUTRAL
    macd_line, signal_line = result
    return Direction.LONG if macd_line > signal_line else Direction.SHORT


def _delta_momentum(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    window = candles[: index + 1]
    value = ind.cvd(window, int(params["period"]))
    if value is None:
        return Direction.NEUTRAL
    threshold = params["threshold"]
    if value > threshold:
        return Direction.LONG
    if value < -threshold:
        return Direction.SHORT
    return Direction.NEUTRAL


def _cvd_trend(params: dict[str, float], candles: Sequence[Candle], index: int) -> Direction:
    window = candles[: index + 1]
    value = ind.cvd(window, int(params["period"]))
    if value is None or value == 0.0:
        return Direction.NEUTRAL
    return Direction.LONG if value > 0.0 else Direction.SHORT


# ---------------------------------------------------------------------------
# Predicados de filtro
# ---------------------------------------------------------------------------


def _session_filter(params: dict[str, float], candles: Sequence[Candle], index: int) -> bool:
    hour = candles[index].start.hour
    start = int(params["start_hour"]) % 24
    end = int(params["end_hour"]) % 24
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # ventana que cruza medianoche


def _volatility_filter(params: dict[str, float], candles: Sequence[Candle], index: int) -> bool:
    window = candles[: index + 1]
    atr = ind.atr(window, int(params["period"]))
    close = window[-1].close
    if atr is None or close <= 0.0:
        return True  # sin datos: no bloquea el warmup
    atr_pct = atr / close
    return params["min_pct"] <= atr_pct <= params["max_pct"]


def _liquidity_filter(params: dict[str, float], candles: Sequence[Candle], index: int) -> bool:
    window = candles[: index + 1]
    avg = ind.sma([c.volume for c in window], int(params["period"]))
    if avg is None or avg <= 0.0:
        return True
    return window[-1].volume >= params["min_ratio"] * avg


def _trend_regime_filter(params: dict[str, float], candles: Sequence[Candle], index: int) -> bool:
    closes = _closes(candles, index)
    value = ind.momentum(closes, int(params["period"]))
    if value is None:
        return True
    return abs(value) >= params["min_abs_momentum"]


# ---------------------------------------------------------------------------
# Registros
# ---------------------------------------------------------------------------


SIGNAL_BLOCKS: dict[str, BlockSpec] = {
    "ema_cross": BlockSpec(
        kind="ema_cross",
        category="trend",
        defaults={"fast": 9.0, "slow": 21.0},
        ranges={"fast": (3.0, 20.0, True), "slow": (21.0, 80.0, True)},
        predicate=_ema_cross,
    ),
    "sma_cross": BlockSpec(
        kind="sma_cross",
        category="trend",
        defaults={"fast": 10.0, "slow": 30.0},
        ranges={"fast": (3.0, 25.0, True), "slow": (26.0, 100.0, True)},
        predicate=_sma_cross,
    ),
    "donchian_breakout": BlockSpec(
        kind="donchian_breakout",
        category="breakout",
        defaults={"period": 20.0},
        ranges={"period": (10.0, 60.0, True)},
        predicate=_donchian_breakout,
    ),
    "atr_breakout": BlockSpec(
        kind="atr_breakout",
        category="breakout",
        defaults={"period": 14.0, "mult": 1.5},
        ranges={"period": (7.0, 30.0, True), "mult": (0.5, 3.5, False)},
        predicate=_atr_breakout,
    ),
    "momentum": BlockSpec(
        kind="momentum",
        category="momentum",
        defaults={"period": 10.0, "threshold": 0.004},
        ranges={"period": (3.0, 40.0, True), "threshold": (0.0, 0.02, False)},
        predicate=_momentum,
    ),
    "rsi_reversion": BlockSpec(
        kind="rsi_reversion",
        category="reversion",
        defaults={"period": 14.0, "low": 30.0, "high": 70.0},
        ranges={
            "period": (5.0, 30.0, True),
            "low": (15.0, 40.0, False),
            "high": (60.0, 85.0, False),
        },
        predicate=_rsi_reversion,
    ),
    "vwap_reversion": BlockSpec(
        kind="vwap_reversion",
        category="reversion",
        defaults={"period": 30.0, "band": 0.004},
        ranges={"period": (10.0, 80.0, True), "band": (0.001, 0.02, False)},
        predicate=_vwap_reversion,
    ),
    "macd": BlockSpec(
        kind="macd",
        category="momentum",
        defaults={"fast": 12.0, "slow": 26.0, "signal": 9.0},
        ranges={"fast": (5.0, 15.0, True), "slow": (20.0, 40.0, True), "signal": (5.0, 12.0, True)},
        predicate=_macd,
    ),
    "delta_momentum": BlockSpec(
        kind="delta_momentum",
        category="orderflow",
        defaults={"period": 10.0, "threshold": 0.0},
        ranges={"period": (3.0, 40.0, True), "threshold": (0.0, 500.0, False)},
        predicate=_delta_momentum,
        needs_orderflow=True,
    ),
    "cvd_trend": BlockSpec(
        kind="cvd_trend",
        category="orderflow",
        defaults={"period": 20.0},
        ranges={"period": (5.0, 60.0, True)},
        predicate=_cvd_trend,
        needs_orderflow=True,
    ),
}


CONTEXT_FILTERS: dict[str, FilterSpec] = {
    "session": FilterSpec(
        kind="session",
        category="time",
        defaults={"start_hour": 7.0, "end_hour": 20.0},
        ranges={"start_hour": (0.0, 23.0, True), "end_hour": (0.0, 23.0, True)},
        predicate=_session_filter,
    ),
    "volatility": FilterSpec(
        kind="volatility",
        category="volatility",
        defaults={"period": 14.0, "min_pct": 0.0005, "max_pct": 0.05},
        ranges={
            "period": (7.0, 30.0, True),
            "min_pct": (0.0, 0.003, False),
            "max_pct": (0.01, 0.10, False),
        },
        predicate=_volatility_filter,
    ),
    "liquidity": FilterSpec(
        kind="liquidity",
        category="liquidity",
        defaults={"period": 20.0, "min_ratio": 0.6},
        ranges={"period": (10.0, 60.0, True), "min_ratio": (0.2, 1.5, False)},
        predicate=_liquidity_filter,
    ),
    "trend_regime": FilterSpec(
        kind="trend_regime",
        category="regime",
        defaults={"period": 30.0, "min_abs_momentum": 0.002},
        ranges={"period": (10.0, 80.0, True), "min_abs_momentum": (0.0, 0.01, False)},
        predicate=_trend_regime_filter,
    ),
}


# Filtros que encajan naturalmente con cada familia de bloque (reglas de
# composición: el generador no empareja al azar). Un breakout quiere tendencia y
# volatilidad; una reversión quiere volatilidad acotada; el order flow, liquidez.
FILTER_AFFINITY: dict[str, tuple[str, ...]] = {
    "trend": ("trend_regime", "session"),
    "breakout": ("volatility", "trend_regime"),
    "momentum": ("trend_regime", "volatility"),
    "reversion": ("volatility", "liquidity"),
    "orderflow": ("liquidity", "session"),
}
