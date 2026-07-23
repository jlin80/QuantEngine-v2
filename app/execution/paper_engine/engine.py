"""Paper Engine: ejecuta órdenes contra el mercado sin arriesgar capital.

Simula con la mayor fidelidad posible una ejecución real: usa bid/ask reales,
aplica spread, slippage, latencia (con deriva de precio), comisiones, rechazos
aleatorios, ejecución parcial y gaps. **Nunca asume ejecución perfecta** y
jamás toca un broker real: es el corazón de la regla de oro de la Fase 5.
"""

import logging
import random

from app.config.settings import ExecutionSettings
from app.execution.commission import CommissionEngine
from app.execution.latency import LatencyEngine
from app.execution.models import (
    BrokerExecution,
    Fill,
    OrderRequest,
    OrderSide,
    OrderType,
    RejectReason,
)
from app.execution.slippage import SlippageContext, SlippageEngine
from app.market.models import Ticker
from app.utils.time import utc_now

PaperExecution = BrokerExecution
"""Nombre histórico (Fase 5) del resultado de ejecución; hoy es el modelo común."""


class PaperBroker:
    """Simulated execution venue for paper trading.

    Implements :class:`~app.core.interfaces.broker.ExecutionBroker`. It is the
    only broker the engine can select: live trading stays disabled.

    Args:
        settings: Execution configuration (probabilidades, gaps, TIF).
        commission: Motor de comisiones.
        slippage: Motor de slippage.
        latency: Motor de latencia.
        rng: Fuente aleatoria inyectable (tests deterministas).
    """

    def __init__(
        self,
        settings: ExecutionSettings,
        commission: CommissionEngine,
        slippage: SlippageEngine,
        latency: LatencyEngine,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._commission = commission
        self._slippage = slippage
        self._latency = latency
        self._rng = rng or random.Random()
        self._executed = 0
        self._rejected = 0
        self._log = logging.getLogger("app.execution.paper")

    @property
    def broker_name(self) -> str:
        """Short broker identifier."""
        return "paper"

    @property
    def stats(self) -> dict[str, int]:
        """Execution counters."""
        return {"executed": self._executed, "rejected": self._rejected}

    def healthcheck(self) -> bool:
        """Always usable: the simulator has no external dependency."""
        return True

    def execute(
        self,
        request: OrderRequest,
        ticker: Ticker,
        context: SlippageContext | None = None,
        *,
        allow_reject: bool = True,
        allow_partial: bool = True,
    ) -> BrokerExecution:
        """Attempt to execute an order against the current market.

        Args:
            request: Intención de operar.
            ticker: Mejor bid/ask del símbolo.
            context: Contexto de slippage (volatilidad, liquidez, sesión).
            allow_reject: Si permite el rechazo aleatorio simulado. Los cierres
                lo desactivan: una salida nunca debe quedarse atascada.
            allow_partial: Si permite la ejecución parcial simulada.

        Returns:
            Un :class:`BrokerExecution` con el fill o el motivo del rechazo.
        """
        if request.quantity <= 0:
            self._rejected += 1
            return BrokerExecution(None, RejectReason.INVALID_QUANTITY)
        if ticker.bid <= 0 or ticker.ask <= 0 or ticker.ask < ticker.bid:
            self._rejected += 1
            return BrokerExecution(None, RejectReason.NO_MARKET_DATA)
        if allow_reject and self._rng.random() < self._settings.reject_probability:
            self._rejected += 1
            self._log.info("Simulated reject for %s %s", request.symbol, request.side.value)
            return BrokerExecution(None, RejectReason.SIMULATED_REJECT)

        reference = self._reference_price(request, ticker)
        if reference is None:
            self._rejected += 1
            return BrokerExecution(None, RejectReason.NO_MARKET_DATA)

        slip_ctx = context or SlippageContext(
            spread_bps=ticker.spread_bps, order_quantity=request.quantity
        )
        slippage_bps = self._slippage.estimate_bps(slip_ctx)
        latency = self._latency.sample()
        adverse_bps = slippage_bps + latency.drift_bps
        price = self._apply_adverse(reference, request.side, adverse_bps)
        gapped = adverse_bps >= self._settings.gap_threshold_bps

        quantity = self._fill_quantity(request.quantity) if allow_partial else request.quantity
        maker = request.order_type == OrderType.LIMIT
        commission = self._commission.calculate(request.symbol, quantity, price, maker=maker).amount

        fill = Fill(
            request_id=request.request_id,
            symbol=request.symbol,
            side=request.side,
            quantity=quantity,
            requested_quantity=request.quantity,
            reference_price=reference,
            price=round(price, 8),
            slippage_bps=slippage_bps,
            spread_bps=ticker.spread_bps,
            commission=commission,
            latency_ms=latency.total_ms,
            liquidity="maker" if maker else "taker",
            gapped=gapped,
            executed_at=utc_now(),
        )
        self._executed += 1
        return BrokerExecution(fill)

    def _reference_price(self, request: OrderRequest, ticker: Ticker) -> float | None:
        """Resolve the pre-cost reference price for the order type."""
        buy = request.side is OrderSide.BUY
        touch = ticker.ask if buy else ticker.bid
        if request.order_type == OrderType.MARKET:
            return touch
        if request.order_type == OrderType.LIMIT and request.limit_price is not None:
            # Marketable sólo si el precio ya alcanzó el límite.
            marketable = touch <= request.limit_price if buy else touch >= request.limit_price
            return request.limit_price if marketable else None
        if request.order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT):
            stop = request.stop_price
            if stop is None:
                return None
            triggered = touch >= stop if buy else touch <= stop
            return touch if triggered else None
        return touch

    def _apply_adverse(self, reference: float, side: OrderSide, adverse_bps: float) -> float:
        """Move the price against the taker by ``adverse_bps``."""
        factor = adverse_bps / 10_000.0
        if side is OrderSide.BUY:
            return reference * (1.0 + factor)
        return reference * (1.0 - factor)

    def _fill_quantity(self, quantity: float) -> float:
        """Return the filled quantity, occasionally partial."""
        if self._rng.random() < self._settings.partial_fill_probability:
            fraction = self._rng.uniform(0.5, 0.9)
            return round(quantity * fraction, 8)
        return quantity
