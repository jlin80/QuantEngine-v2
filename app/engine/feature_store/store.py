"""Feature Store: repositorio central de variables de mercado.

Regla: ninguna variable se calcula dos veces en la misma ventana. Hay dos
registros: features escalares (``float``) y features objeto (análisis
compuestos: SMC, estructura, volume profile, order flow, liquidez, bandas
VWAP). Ambos comparten el cache TTL por (feature, símbolo, parámetros) y las
métricas de aciertos/fallos para detectar cálculo duplicado.
"""

import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from app.analytics.indicators import (
    acceleration,
    adaptive_atr,
    analyze_liquidity,
    analyze_order_flow,
    analyze_smc,
    analyze_structure,
    anchor_index_at,
    atr_slope,
    expansion_ratio,
    momentum_score,
    rate_of_change,
    session_anchor,
    volume_profile,
    vwap_bands,
)
from app.market.models import Candle, Timeframe, Trade, TradeSide
from app.market.services import MarketDataService
from app.utils.time import utc_now

FeatureFn = Callable[[str, dict[str, Any]], Awaitable[float | None]]
"""Proveedor de feature escalar: ``(symbol, params) -> valor``."""

ObjectFeatureFn = Callable[[str, dict[str, Any]], Awaitable[object]]
"""Proveedor de feature objeto: ``(symbol, params) -> análisis | None``."""


class FeatureStore:
    """Compute-once cache of market features (escalares y objetos).

    Args:
        market: API de datos agnóstica del broker.
        default_ttl_seconds: Vida del valor cacheado por defecto.
    """

    def __init__(self, market: MarketDataService, *, default_ttl_seconds: float = 1.0) -> None:
        self._market = market
        self._default_ttl = default_ttl_seconds
        self._providers: dict[str, tuple[FeatureFn, float]] = {}
        self._object_providers: dict[str, tuple[ObjectFeatureFn, float]] = {}
        self._cache: dict[tuple[str, str, str], tuple[float, object]] = {}
        self._hits = 0
        self._misses = 0
        self._log = logging.getLogger("app.engine.features")
        self._register_builtin()

    # ------------------------------------------------------------------
    # Registro y acceso
    # ------------------------------------------------------------------

    def register(self, name: str, fn: FeatureFn, *, ttl_seconds: float | None = None) -> None:
        """Register (or replace) a scalar feature provider.

        Args:
            name: Nombre único de la feature.
            fn: Corrutina ``(symbol, params) -> valor``.
            ttl_seconds: TTL del cache para esta feature.
        """
        self._providers[name] = (fn, ttl_seconds or self._default_ttl)

    def register_object(
        self, name: str, fn: ObjectFeatureFn, *, ttl_seconds: float | None = None
    ) -> None:
        """Register (or replace) a composite/object feature provider."""
        self._object_providers[name] = (fn, ttl_seconds or self._default_ttl)

    @property
    def available(self) -> list[str]:
        """Nombres de las features escalares registradas."""
        return sorted(self._providers)

    @property
    def available_objects(self) -> list[str]:
        """Nombres de las features objeto registradas."""
        return sorted(self._object_providers)

    async def get(self, name: str, symbol: str, **params: Any) -> float | None:
        """Return a scalar feature, computing it at most once per TTL window.

        Args:
            name: Feature registrada.
            symbol: Símbolo.
            **params: Parámetros del cálculo (p. ej. ``period=14``).

        Returns:
            El valor, o ``None`` si no hay datos suficientes.

        Raises:
            KeyError: Si la feature no está registrada.
        """
        fn, ttl = self._providers[name]
        value = await self._cached(name, symbol, params, ttl, fn)
        return cast("float | None", value)

    async def get_object(self, name: str, symbol: str, **params: Any) -> object:
        """Return a composite feature (análisis tipado) con el mismo cache.

        Raises:
            KeyError: Si la feature objeto no está registrada.
        """
        fn, ttl = self._object_providers[name]
        return await self._cached(f"obj:{name}", symbol, params, ttl, fn)

    async def _cached(
        self,
        name: str,
        symbol: str,
        params: dict[str, Any],
        ttl: float,
        fn: Callable[[str, dict[str, Any]], Awaitable[object]],
    ) -> object:
        """Shared TTL cache for both provider kinds."""
        key = (name, symbol.upper(), repr(sorted(params.items())))
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] < ttl:
            self._hits += 1
            return cached[1]
        self._misses += 1
        value = await fn(symbol.upper(), params)
        self._cache[key] = (now, value)
        return value

    async def get_many(
        self, names: list[str], symbol: str, **params: Any
    ) -> dict[str, float | None]:
        """Fetch several scalar features at once (mismos params para todas)."""
        return {name: await self.get(name, symbol, **params) for name in names}

    def invalidate(self, symbol: str | None = None) -> None:
        """Drop cached values (por símbolo o todos)."""
        if symbol is None:
            self._cache.clear()
            return
        upper = symbol.upper()
        for key in [k for k in self._cache if k[1] == upper]:
            del self._cache[key]

    @property
    def stats(self) -> dict[str, int]:
        """Cache hit/miss counters (para detectar cálculo duplicado)."""
        return {"hits": self._hits, "misses": self._misses, "cached": len(self._cache)}

    # ------------------------------------------------------------------
    # Features integradas
    # ------------------------------------------------------------------

    def _register_builtin(self) -> None:
        """Register the built-in feature set."""
        self.register("last_price", self._last_price)
        self.register("spread_bps", self._spread_bps)
        self.register("atr", self._atr)
        self.register("atr_pct", self._atr_pct)
        self.register("atr_adaptive", self._atr_adaptive)
        self.register("atr_slope", self._atr_slope)
        self.register("atr_expansion", self._atr_expansion)
        self.register("ema", self._ema)
        self.register("vwap", self._vwap)
        self.register("vwap_session", self._vwap_session)
        self.register("vwap_slope", self._vwap_slope)
        self.register("vwap_distance_pct", self._vwap_distance_pct)
        self.register("roc", self._roc)
        self.register("momentum_score", self._momentum_score)
        self.register("acceleration", self._acceleration)
        self.register("volume", self._volume)
        self.register("volume_ratio", self._volume_ratio)
        self.register("delta", self._delta)
        self.register("cvd", self._cvd)
        self.register("book_pressure", self._book_pressure)
        self.register("imbalance", self._imbalance)
        self.register("open_interest", self._open_interest)
        self.register("funding_rate", self._funding_rate)

        self.register_object("structure", self._structure)
        self.register_object("smc", self._smc)
        self.register_object("volume_profile", self._volume_profile)
        self.register_object("orderflow", self._orderflow)
        self.register_object("liquidity", self._liquidity)
        self.register_object("vwap_bands", self._vwap_bands)

    def _tf(self, params: dict[str, Any]) -> Timeframe:
        """Timeframe param (default 1m)."""
        raw = params.get("timeframe", "1m")
        return raw if isinstance(raw, Timeframe) else Timeframe(str(raw))

    def _candles(self, symbol: str, params: dict[str, Any], default_lookback: int) -> list[Candle]:
        """Candle window for a feature computation."""
        lookback = int(params.get("lookback", default_lookback))
        return self._market.get_candles(symbol, self._tf(params), lookback)

    # --- escalares -----------------------------------------------------

    async def _last_price(self, symbol: str, params: dict[str, Any]) -> float | None:
        return await self._market.get_last_price(symbol)

    async def _spread_bps(self, symbol: str, params: dict[str, Any]) -> float | None:
        ticker = self._market.get_ticker(symbol)
        return ticker.spread_bps if ticker is not None else None

    async def _atr(self, symbol: str, params: dict[str, Any]) -> float | None:
        period = int(params.get("period", 14))
        candles = self._market.get_candles(symbol, self._tf(params), period + 1)
        if len(candles) < 2:
            return None
        true_ranges: list[float] = []
        for prev, curr in itertools.pairwise(candles):
            true_ranges.append(
                max(
                    curr.high - curr.low,
                    abs(curr.high - prev.close),
                    abs(curr.low - prev.close),
                )
            )
        window = true_ranges[-period:]
        return sum(window) / len(window) if window else None

    async def _atr_pct(self, symbol: str, params: dict[str, Any]) -> float | None:
        atr = await self._atr(symbol, params)
        price = await self._last_price(symbol, params)
        if atr is None or price is None or price <= 0:
            return None
        return atr / price * 100.0

    async def _atr_adaptive(self, symbol: str, params: dict[str, Any]) -> float | None:
        period = int(params.get("period", 14))
        candles = self._candles(symbol, params, period * 4)
        return adaptive_atr(candles, period)

    async def _atr_slope(self, symbol: str, params: dict[str, Any]) -> float | None:
        period = int(params.get("period", 14))
        lookback = int(params.get("slope_lookback", 5))
        candles = self._candles(symbol, params, period + lookback + 5)
        return atr_slope(candles, period, lookback)

    async def _atr_expansion(self, symbol: str, params: dict[str, Any]) -> float | None:
        short = int(params.get("short_period", 5))
        long = int(params.get("long_period", 20))
        candles = self._candles(symbol, params, long + 1)
        return expansion_ratio(candles, short, long)

    async def _ema(self, symbol: str, params: dict[str, Any]) -> float | None:
        period = int(params.get("period", 20))
        candles = self._market.get_candles(symbol, self._tf(params), period * 3)
        if len(candles) < period:
            return None
        alpha = 2.0 / (period + 1)
        ema = candles[0].close
        for candle in candles[1:]:
            ema = alpha * candle.close + (1 - alpha) * ema
        return ema

    async def _vwap(self, symbol: str, params: dict[str, Any]) -> float | None:
        lookback = int(params.get("lookback", 60))
        candles = self._market.get_candles(symbol, self._tf(params), lookback)
        notional = sum(c.vwap * c.volume for c in candles if c.volume > 0)
        volume = sum(c.volume for c in candles)
        return notional / volume if volume > 0 else None

    async def _session_bands(self, symbol: str, params: dict[str, Any]) -> object:
        """Bandas VWAP de la sesión (day/week/month) — base de los escalares."""
        anchor_kind = str(params.get("anchor", "day"))
        candles = self._candles(symbol, params, 500)
        if not candles:
            return None
        anchor = session_anchor(anchor_kind, utc_now())
        index = anchor_index_at(candles, anchor)
        if index is None:
            return None
        price = await self._last_price(symbol, params)
        return vwap_bands(
            candles,
            index,
            slope_lookback=int(params.get("slope_lookback", 10)),
            reference_price=price,
        )

    async def _vwap_session(self, symbol: str, params: dict[str, Any]) -> float | None:
        bands = await self.get_object("vwap_bands", symbol, **params)
        from app.analytics.indicators import VWAPBands

        return bands.value if isinstance(bands, VWAPBands) else None

    async def _vwap_slope(self, symbol: str, params: dict[str, Any]) -> float | None:
        bands = await self.get_object("vwap_bands", symbol, **params)
        from app.analytics.indicators import VWAPBands

        return bands.slope_pct_per_bar if isinstance(bands, VWAPBands) else None

    async def _vwap_distance_pct(self, symbol: str, params: dict[str, Any]) -> float | None:
        bands = await self.get_object("vwap_bands", symbol, **params)
        from app.analytics.indicators import VWAPBands

        return bands.distance_pct if isinstance(bands, VWAPBands) else None

    async def _roc(self, symbol: str, params: dict[str, Any]) -> float | None:
        period = int(params.get("period", 10))
        candles = self._candles(symbol, params, period + 1)
        return rate_of_change(candles, period)

    async def _momentum_score(self, symbol: str, params: dict[str, Any]) -> float | None:
        period = int(params.get("period", 10))
        candles = self._candles(symbol, params, max(period, 14) * 3)
        return momentum_score(candles, period, int(params.get("atr_period", 14)))

    async def _acceleration(self, symbol: str, params: dict[str, Any]) -> float | None:
        period = int(params.get("period", 10))
        candles = self._candles(symbol, params, period * 3 + 1)
        return acceleration(candles, period)

    async def _volume(self, symbol: str, params: dict[str, Any]) -> float | None:
        lookback = int(params.get("lookback", 20))
        candles = self._market.get_candles(symbol, self._tf(params), lookback)
        return sum(c.volume for c in candles) if candles else None

    async def _volume_ratio(self, symbol: str, params: dict[str, Any]) -> float | None:
        """Volumen de la última vela vs la media de la ventana previa."""
        lookback = int(params.get("lookback", 20))
        candles = self._candles(symbol, params, lookback + 1)
        if len(candles) < 2:
            return None
        prior = candles[:-1]
        avg = sum(c.volume for c in prior) / len(prior)
        return candles[-1].volume / avg if avg > 0 else None

    async def _delta(self, symbol: str, params: dict[str, Any]) -> float | None:
        limit = int(params.get("trades", 200))
        trades = self._market.get_recent_trades(symbol, limit)
        if not trades:
            # Mismo fallback que la feature objeto `orderflow`: sin tape, el
            # volumen firmado de las velas es la única direccionalidad que hay.
            # `None` se conserva si tampoco hay velas con lado — ausencia de
            # medición, no un cero.
            trades = self._flow_from_candles(symbol, params)
        if not trades:
            return None
        return sum(
            t.size if t.side is TradeSide.BUY else -t.size if t.side is TradeSide.SELL else 0.0
            for t in trades
        )

    async def _cvd(self, symbol: str, params: dict[str, Any]) -> float | None:
        # CVD = delta acumulado sobre toda la ventana retenida.
        return await self._delta(symbol, {**params, "trades": int(params.get("trades", 10_000))})

    async def _book_pressure(self, symbol: str, params: dict[str, Any]) -> float | None:
        book = self._market.get_orderbook(symbol)
        return book.book_pressure(int(params.get("levels", 10))) if book else None

    async def _imbalance(self, symbol: str, params: dict[str, Any]) -> float | None:
        book = self._market.get_orderbook(symbol)
        return book.imbalance(int(params.get("levels", 10))) if book else None

    async def _open_interest(self, symbol: str, params: dict[str, Any]) -> float | None:
        oi = self._market.get_open_interest(symbol)
        return oi.contracts if oi is not None else None

    async def _funding_rate(self, symbol: str, params: dict[str, Any]) -> float | None:
        funding = self._market.get_funding_rate(symbol)
        return funding.rate if funding is not None else None

    # --- objetos (análisis compuestos) ----------------------------------

    async def _structure(self, symbol: str, params: dict[str, Any]) -> object:
        candles = self._candles(symbol, params, 120)
        return analyze_structure(
            candles,
            swing_left=int(params.get("swing_left", 3)),
            swing_right=int(params.get("swing_right", 3)),
            range_window=int(params.get("range_window", 20)),
            consolidation_band_pct=float(params.get("consolidation_band_pct", 1.0)),
        )

    async def _smc(self, symbol: str, params: dict[str, Any]) -> object:
        candles = self._candles(symbol, params, 120)
        return analyze_smc(
            candles,
            swing_left=int(params.get("swing_left", 3)),
            swing_right=int(params.get("swing_right", 3)),
            fvg_min_atr=float(params.get("fvg_min_atr", 0.3)),
            ob_displacement_atr=float(params.get("ob_displacement_atr", 1.5)),
            sweep_scan=int(params.get("sweep_scan", 5)),
            sweep_tolerance_pct=float(params.get("sweep_tolerance_pct", 0.02)),
            equal_tolerance_pct=float(params.get("equal_tolerance_pct", 0.05)),
            mss_displacement_atr=float(params.get("mss_displacement_atr", 1.5)),
            atr_period=int(params.get("atr_period", 14)),
        )

    async def _volume_profile(self, symbol: str, params: dict[str, Any]) -> object:
        candles = self._candles(symbol, params, 120)
        return volume_profile(
            candles,
            bins=int(params.get("bins", 24)),
            value_area_pct=float(params.get("value_area_pct", 0.70)),
            node_ratio=float(params.get("node_ratio", 1.5)),
        )

    async def _orderflow(self, symbol: str, params: dict[str, Any]) -> object:
        trades = self._market.get_recent_trades(symbol, int(params.get("trades", 200)))
        book = self._market.get_orderbook(symbol)
        if not trades:
            # Sin tape (MT5 nunca emite `Trade`: su polling sólo publica
            # `Ticker`), esta feature salía vacía y `delta_confirmation` y `cvd`
            # no emitían una sola señal — verificado en producción el
            # 2026-08-25, cuatro días después de poblar el volumen firmado de
            # las velas. Poblar la vela era necesario pero no suficiente: los
            # indicadores leen de aquí.
            trades = self._flow_from_candles(symbol, params)
        return analyze_order_flow(
            trades,
            book,
            buckets=int(params.get("buckets", 6)),
            absorption_move_pct=float(params.get("absorption_move_pct", 0.05)),
            absorption_min_ratio=float(params.get("absorption_min_ratio", 0.65)),
        )

    def _flow_from_candles(self, symbol: str, params: dict[str, Any]) -> list[Trade]:
        """Synthesize aggressive flow from the signed volume of recent candles.

        Cada vela aporta **dos** agresiones sintéticas —una compradora y una
        vendedora, al cierre de la vela y con el tamaño de su ``buy_volume`` /
        ``sell_volume``—, que es toda la resolución direccional que hay cuando
        el venue no publica operaciones. El volumen firmado lo produce la regla
        del tick del agregador (``CandleAggregator.add_ticker``).

        **Es un proxy de presión de cotización, no de agresión ejecutada.** No
        recupera el tamaño de las órdenes ni su secuencia real, así que las
        heurísticas del indicador que dependen de trades individuales (iceberg,
        trades grandes) quedan sin fundamento aquí. Delta, CVD y agresión sí son
        interpretables.

        Devuelve vacío si las velas tampoco traen volumen firmado, para que la
        feature siga declarando ausencia en vez de inventar un cero.
        """
        candles = self._candles(symbol, params, int(params.get("flow_candles", 60)))
        trades: list[Trade] = []
        for candle in candles:
            for side, size in (
                (TradeSide.BUY, candle.buy_volume),
                (TradeSide.SELL, candle.sell_volume),
            ):
                if size <= 0:
                    continue
                trades.append(
                    Trade(
                        symbol=candle.symbol,
                        provider=candle.provider,
                        price=candle.close,
                        size=size,
                        side=side,
                        exchange_ts=candle.end,
                        local_ts=candle.end,
                    )
                )
        return trades

    async def _liquidity(self, symbol: str, params: dict[str, Any]) -> object:
        candles = self._candles(symbol, params, 120)
        return analyze_liquidity(
            candles,
            swing_left=int(params.get("swing_left", 3)),
            swing_right=int(params.get("swing_right", 3)),
            equal_tolerance_pct=float(params.get("equal_tolerance_pct", 0.05)),
            hunt_scan=int(params.get("hunt_scan", 5)),
            rejection_wick_ratio=float(params.get("rejection_wick_ratio", 0.5)),
        )

    async def _vwap_bands(self, symbol: str, params: dict[str, Any]) -> object:
        return await self._session_bands(symbol, params)
