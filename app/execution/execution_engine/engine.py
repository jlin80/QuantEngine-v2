"""Execution Engine: convierte decisiones aceptadas en operaciones (paper).

Flujo por decisión aceptada:
    decisión → contexto (ATR/spread/régimen) → sizing → Risk Manager →
    Paper Engine → Position Manager → Portfolio Manager → Journal → eventos.

En paralelo, un bucle de gestión valora las posiciones abiertas, aplica
break-even y trailing, y las cierra por stop, objetivo, tiempo o cambio de
régimen. Cada acción se publica como evento; Discord reacciona de forma
desacoplada. **Nunca envía órdenes a un broker real.**
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.config.settings import ExecutionSettings
from app.core.events.base import Event
from app.core.events.bus import EventBus, Subscription
from app.core.interfaces.broker import ExecutionBroker
from app.core.lifecycle import Service
from app.engine.events import DecisionGenerated
from app.engine.market_context import MarketContextEngine
from app.execution import events as ev
from app.execution.commission import CommissionEngine
from app.execution.journal import TradeJournal
from app.execution.models import (
    ExitReason,
    Fill,
    Order,
    OrderRequest,
    OrderSide,
    OrderType,
    Position,
    PositionSide,
    RejectReason,
    TradeRecord,
)
from app.execution.order_manager import OrderManager
from app.execution.performance import PerformanceEngine
from app.execution.portfolio_manager import PortfolioManager
from app.execution.position_manager import PositionManager
from app.execution.risk_manager import RiskManager, RiskQuery
from app.execution.sizing import PositionSizer
from app.execution.slippage import SlippageContext
from app.market.models import Ticker, Timeframe
from app.market.services import MarketDataService
from app.utils.time import utc_now

_SESSION_HOURS: dict[str, tuple[int, int]] = {
    "asia": (0, 9),
    "europe": (7, 16),
    "america": (13, 22),
}


@dataclass(frozen=True, slots=True)
class _MarketView:
    """Contexto de mercado condensado para una decisión/gestión."""

    atr: float | None
    atr_pct: float | None
    spread_bps: float | None
    regime: str
    volatility: str
    volume: float | None
    last_price: float | None
    session: str


class ExecutionEngine(Service):
    """Orchestrate risk-checked paper execution and position management.

    Args:
        settings: Configuración de ejecución.
        market: Servicio de datos de mercado (bid/ask, velas).
        paper: Broker de ejecución. Se tipa contra el protocolo
            ``ExecutionBroker``; hoy sólo puede ser el ``PaperBroker``.
        commission: Motor de comisiones.
        sizer: Motor de position sizing.
        risk: Risk Manager.
        positions: Position Manager.
        portfolio: Portfolio Manager.
        orders: Order Manager.
        journal: Trade Journal.
        performance: Performance Engine (para Kelly y reportes).
        bus: Event Bus (``None`` en tests puros).
        context: Market Context Engine (opcional; enriquece ATR/régimen).
    """

    def __init__(
        self,
        settings: ExecutionSettings,
        market: MarketDataService,
        paper: ExecutionBroker,
        commission: CommissionEngine,
        sizer: PositionSizer,
        risk: RiskManager,
        positions: PositionManager,
        portfolio: PortfolioManager,
        orders: OrderManager,
        journal: TradeJournal,
        performance: PerformanceEngine,
        bus: EventBus | None = None,
        context: MarketContextEngine | None = None,
    ) -> None:
        super().__init__("execution_engine")
        self._settings = settings
        self._market = market
        self._paper = paper
        self._commission = commission
        self._sizer = sizer
        self._risk = risk
        self._positions = positions
        self._portfolio = portfolio
        self._orders = orders
        self._journal = journal
        self._performance = performance
        self._bus = bus
        self._context = context
        self._subscription: Subscription | None = None
        self._manage_task: asyncio.Task[None] | None = None
        self._kill_announced = False
        self._cb_announced = False
        self._entry_vetoes: list[Callable[[], str | None]] = []
        self._log = logging.getLogger("app.execution.engine")

    # ------------------------------------------------------------------
    # Vetos de entrada (hook genérico; Safe Mode lo usa en Fase 9)
    # ------------------------------------------------------------------

    def register_entry_veto(self, veto: Callable[[], str | None]) -> None:
        """Register a callback that can block new entries.

        El motor de ejecución no conoce a quién veta: la capa de producción se
        registra aquí sin que ejecución dependa de ella.

        Args:
            veto: Callable returning a blocking reason, or ``None`` to allow.
        """
        self._entry_vetoes.append(veto)

    def _entry_veto(self) -> str | None:
        """Return the first blocking reason among registered vetoes."""
        for veto in self._entry_vetoes:
            try:
                reason = veto()
            except Exception:
                # Un veto que falla se trata como bloqueante: fail-closed.
                self._log.exception("Veto de entrada falló; se bloquea por precaución")
                return "veto de entrada con error interno"
            if reason is not None:
                return reason
        return None

    # ------------------------------------------------------------------
    # Acceso a los sub-componentes (dashboard/diagnóstico/tests)
    # ------------------------------------------------------------------

    @property
    def positions(self) -> PositionManager:
        """Position Manager."""
        return self._positions

    @property
    def portfolio(self) -> PortfolioManager:
        """Portfolio Manager."""
        return self._portfolio

    @property
    def risk(self) -> RiskManager:
        """Risk Manager."""
        return self._risk

    @property
    def orders(self) -> OrderManager:
        """Order Manager."""
        return self._orders

    @property
    def journal(self) -> TradeJournal:
        """Trade Journal."""
        return self._journal

    @property
    def broker(self) -> ExecutionBroker:
        """Broker de ejecución activo."""
        return self._paper

    @property
    def paper(self) -> ExecutionBroker:
        """Alias histórico (Fase 5) de :attr:`broker`."""
        return self._paper

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _on_start(self) -> None:
        """Subscribe to decisions and launch the management loop."""
        if self._bus is not None:
            self._subscription = self._bus.subscribe(self._on_event, DecisionGenerated)
        interval = max(0.2, self._settings.manage_interval_seconds)
        self._manage_task = asyncio.create_task(self._manage_loop(interval), name="exec-manage")

    async def _on_stop(self) -> None:
        """Unsubscribe and cancel the management loop."""
        if self._bus is not None and self._subscription is not None:
            self._bus.unsubscribe(self._subscription)
            self._subscription = None
        if self._manage_task is not None:
            self._manage_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._manage_task
            self._manage_task = None

    async def _manage_loop(self, interval: float) -> None:
        """Periodically manage open positions until cancelled."""
        while True:
            await asyncio.sleep(interval)
            try:
                await self.manage_once()
            except Exception:
                self._log.exception("Error en el bucle de gestión de posiciones")

    async def _on_event(self, event: Event) -> None:
        """Bus adapter: route accepted decisions into the entry flow."""
        if isinstance(event, DecisionGenerated):
            await self.process_decision(event)

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------

    async def process_decision(self, decision: DecisionGenerated) -> Position | None:
        """Try to open a position from an accepted decision.

        Args:
            decision: Evento de decisión del Decision Engine.

        Returns:
            La posición abierta, o ``None`` si no se operó.
        """
        if not decision.accepted or decision.action not in ("open_long", "open_short"):
            return None
        symbol = decision.symbol.upper()
        side = OrderSide.BUY if decision.action == "open_long" else OrderSide.SELL
        # Vetos externos (Safe Mode en Fase 9). Se evalúan antes que nada: si el
        # sistema está degradado no tiene sentido ni mirar el mercado.
        veto = self._entry_veto()
        if veto is not None:
            await self._reject(symbol, side, RejectReason.RISK_BLOCKED, "entry_veto", veto)
            return None
        ticker = self._market.get_ticker(symbol)
        if ticker is None:
            self._log.info("Sin ticker para %s: no se opera", symbol)
            return None

        view = await self._market_view(symbol)
        spread = self._risk.check_spread(view.spread_bps)
        if not spread.allowed:
            await self._reject(
                symbol, side, RejectReason.SPREAD_TOO_WIDE, spread.rule, spread.reason
            )
            return None
        liquidity = self._risk.check_liquidity(view.volume)
        if not liquidity.allowed:
            await self._reject(
                symbol, side, RejectReason.RISK_BLOCKED, liquidity.rule, liquidity.reason
            )
            return None

        reference = ticker.ask if side is OrderSide.BUY else ticker.bid
        stop_distance = self._stop_distance(view, reference)
        stop_loss, take_profit = self._stops(side, reference, stop_distance)

        equity = self._portfolio.equity(self._positions.open_positions)
        win_rate, reward_risk = self._perf_inputs()
        sizing = self._sizer.calculate(
            equity=equity,
            price=reference,
            stop_distance=stop_distance,
            confidence=decision.confidence,
            win_rate=win_rate,
            reward_risk=reward_risk,
        )
        if sizing.quantity <= 0:
            await self._reject(symbol, side, RejectReason.INVALID_QUANTITY, "sizing", sizing.reason)
            return None

        notional = sizing.quantity * reference
        check = self._risk.evaluate_entry(
            RiskQuery(
                symbol=symbol,
                new_notional=notional,
                equity=equity,
                open_positions=len(self._positions.open_positions),
                positions_on_symbol=len(self._positions.positions_for(symbol)),
                symbol_exposures=self._symbol_exposures(),
            )
        )
        if not check.allowed:
            await self._reject(symbol, side, RejectReason.RISK_BLOCKED, check.rule, check.reason)
            return None

        request = OrderRequest(
            symbol=symbol,
            side=side,
            quantity=sizing.quantity,
            order_type=OrderType.MARKET,
            stop_loss=stop_loss,
            take_profit=take_profit,
            decision_id=decision.decision_id,
            reason=decision.summary,
        )
        order = self._orders.create(request)
        await self._publish(
            ev.OrderCreated(
                source="execution_engine",
                order_id=order.order_id,
                symbol=symbol,
                side=side.value,
                quantity=sizing.quantity,
                order_type=request.order_type.value,
                decision_id=decision.decision_id,
            )
        )
        execution = self._paper.execute(request, ticker, self._slip_context(view, sizing.quantity))
        if not execution.accepted or execution.fill is None:
            self._orders.reject(order, execution.reject_reason)
            await self._publish(
                ev.OrderRejected(
                    source="execution_engine",
                    order_id=order.order_id,
                    symbol=symbol,
                    side=side.value,
                    reason=execution.reject_reason.value,
                )
            )
            return None

        fill = execution.fill
        self._orders.apply_fill(order, fill)
        await self._announce_fill(order, fill)
        self._portfolio.on_open_commission(fill.commission)
        position = self._positions.open(
            fill,
            stop_loss=stop_loss,
            take_profit=take_profit,
            decision_id=decision.decision_id,
            regime=view.regime,
            score=decision.score,
            confidence=decision.confidence,
            entry_reasons=(decision.summary,) if decision.summary else (),
            atr=view.atr,
            volatility=view.volatility,
        )
        await self._publish(
            ev.PositionOpened(
                source="execution_engine",
                position_id=position.position_id,
                symbol=symbol,
                side=position.side.value,
                quantity=position.quantity,
                entry_price=position.entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                decision_id=decision.decision_id,
            )
        )
        self._log.info(
            "Posición abierta %s %s qty=%g @ %.4f (SL=%s TP=%s)",
            symbol,
            position.side.value,
            position.quantity,
            position.entry_price,
            _fmt(stop_loss),
            _fmt(take_profit),
        )
        return position

    # ------------------------------------------------------------------
    # Gestión de posiciones abiertas
    # ------------------------------------------------------------------

    async def manage_once(self) -> None:
        """Run one management pass over every open position."""
        for position in self._positions.open_positions:
            ticker = self._market.get_ticker(position.symbol)
            if ticker is None:
                continue
            mark = self._mark_price(position, ticker)
            self._positions.update_mark(position, mark)
            atr = self._symbol_atr(position.symbol)
            for update in self._positions.manage(position, atr):
                await self._announce_stop(position, update)
            reason = await self._exit_reason(position)
            if reason is not None:
                await self.close_position(position, reason)
        await self._refresh_risk()

    async def _refresh_risk(self) -> None:
        """Update drawdown-driven risk state and flatten on kill switch."""
        snapshot = self._portfolio.snapshot(self._positions.open_positions)
        self._risk.update_equity(snapshot.drawdown_pct)
        active = self._risk.kill_switch_active
        # El latch evita anunciar el mismo disparo en cada vuelta del bucle,
        # pero debe rearmarse al liberar el switch: si no, un segundo disparo
        # tras un `reset_kill_switch()` no volvería a aplanar posiciones.
        if not active:
            self._kill_announced = False
            return
        if not self._kill_announced:
            self._kill_announced = True
            await self._publish(
                ev.KillSwitchTriggered(
                    source="execution_engine",
                    reason=str(self._risk.status().get("kill_reason") or "drawdown"),
                    drawdown_pct=snapshot.drawdown_pct,
                )
            )
        # Aplanar en cada vuelta mientras el switch siga activo: si una posición
        # se abrió justo antes del disparo, no puede quedarse viva.
        for position in self._positions.open_positions:
            await self.close_position(position, ExitReason.KILL_SWITCH)

    async def _exit_reason(self, position: Position) -> ExitReason | None:
        """Decide whether a position must exit right now."""
        max_minutes = self._settings.max_holding_minutes
        if max_minutes > 0 and position.holding_seconds() / 60.0 >= max_minutes:
            return ExitReason.TIME_EXIT
        regime_exit = (
            self._settings.exit_on_regime_change
            and self._context is not None
            and position.regime not in ("", "unknown")
        )
        if regime_exit:
            view = await self._market_view(position.symbol)
            if view.regime not in ("unknown", position.regime):
                return ExitReason.REGIME_CHANGE
        return self._positions.check_exit(position)

    async def close_position(
        self, position: Position, reason: ExitReason, *, exit_reasons: tuple[str, ...] = ()
    ) -> None:
        """Close a position through the Paper Engine and settle it.

        Args:
            position: Posición abierta a cerrar.
            reason: Motivo de la salida.
            exit_reasons: Explicación textual adicional.
        """
        if position.position_id not in {p.position_id for p in self._positions.open_positions}:
            return
        ticker = self._market.get_ticker(position.symbol)
        if ticker is None:
            self._log.warning("Sin ticker para cerrar %s", position.symbol)
            return
        close_side = OrderSide.SELL if position.is_long else OrderSide.BUY
        request = OrderRequest(
            symbol=position.symbol,
            side=close_side,
            quantity=position.quantity,
            order_type=OrderType.MARKET,
            reduce_only=True,
            reason=reason.value,
        )
        order = self._orders.create(request)
        view = await self._market_view(position.symbol)
        # Los cierres nunca se rechazan ni se ejecutan parciales: una salida
        # jamás debe quedar atascada.
        execution = self._paper.execute(
            request,
            ticker,
            self._slip_context(view, position.quantity),
            allow_reject=False,
            allow_partial=False,
        )
        if execution.fill is None:
            return
        fill = execution.fill
        self._orders.apply_fill(order, fill)
        await self._announce_fill(order, fill)
        gross_pnl = self._positions.close(
            position,
            exit_price=fill.price,
            close_commission=fill.commission,
            reason=reason,
            exit_reasons=exit_reasons or (reason.value,),
        )
        self._portfolio.on_trade_closed(gross_pnl=gross_pnl, close_commission=fill.commission)
        self._risk.on_trade_closed(position.realized_pnl)
        if self._risk.circuit_breaker_active and not self._cb_announced:
            self._cb_announced = True
            await self._publish(
                ev.CircuitBreakerTriggered(
                    source="execution_engine",
                    reason="pérdida rápida",
                    loss_pct=self._settings.risk.circuit_breaker_loss_pct,
                    window_minutes=self._settings.risk.circuit_breaker_window_minutes,
                )
            )
        self._journal.record(self._trade_record(position, fill))
        await self._publish(
            ev.PositionClosed(
                source="execution_engine",
                position_id=position.position_id,
                symbol=position.symbol,
                side=position.side.value,
                quantity=position.quantity,
                exit_price=fill.price,
                pnl=round(position.realized_pnl, 6),
                r_multiple=round(position.r_multiple(fill.price), 4),
                exit_reason=reason.value,
                holding_seconds=position.holding_seconds(),
            )
        )
        self._log.info(
            "Posición cerrada %s %s PnL=%.2f (%s)",
            position.symbol,
            position.side.value,
            position.realized_pnl,
            reason.value,
        )

    # ------------------------------------------------------------------
    # APIs de gestión manual
    # ------------------------------------------------------------------

    async def modify_stop(self, position_id: str, stop_loss: float) -> bool:
        """Manually move the stop of an open position."""
        position = self._positions.get(position_id)
        if position is None:
            return False
        previous = position.stop_loss
        position.stop_loss = stop_loss
        await self._publish(
            ev.StopMoved(
                source="execution_engine",
                position_id=position_id,
                symbol=position.symbol,
                previous=previous,
                current=stop_loss,
                reason="manual",
            )
        )
        return True

    async def move_break_even(self, position_id: str) -> bool:
        """Manually move the stop of a position to its entry price."""
        position = self._positions.get(position_id)
        if position is None:
            return False
        position.stop_loss = position.entry_price
        position.break_even_active = True
        await self._publish(
            ev.BreakEvenActivated(
                source="execution_engine",
                position_id=position_id,
                symbol=position.symbol,
                price=position.entry_price,
            )
        )
        return True

    # ------------------------------------------------------------------
    # Helpers de mercado / cálculo
    # ------------------------------------------------------------------

    async def _market_view(self, symbol: str) -> _MarketView:
        """Condense the market context for a symbol (context or candles)."""
        if self._context is not None:
            ctx = await self._context.build(symbol)
            regime = ctx.regime.primary.value if ctx.regime else "unknown"
            session = ctx.sessions[0] if ctx.sessions else "off"
            return _MarketView(
                atr=ctx.atr,
                atr_pct=ctx.atr_pct,
                spread_bps=ctx.spread_bps,
                regime=regime,
                volatility=ctx.volatility.value,
                volume=ctx.volume_recent,
                last_price=ctx.last_price,
                session=session,
            )
        ticker = self._market.get_ticker(symbol)
        atr = self._symbol_atr(symbol)
        last = ticker.mid if ticker else None
        atr_pct = (atr / last * 100.0) if atr and last else None
        spread = ticker.spread_bps if ticker else None
        return _MarketView(
            atr=atr,
            atr_pct=atr_pct,
            spread_bps=spread,
            regime="unknown",
            volatility="normal",
            volume=None,
            last_price=last,
            session=_session_for(utc_now().hour),
        )

    def _symbol_atr(self, symbol: str) -> float | None:
        """ATR of a symbol from recent M1 candles (cheap; loop-friendly)."""
        from app.analytics.indicators.atr import atr as atr_indicator

        period = self._settings.sizing.atr_period
        candles = self._market.get_candles(symbol, Timeframe.M1, limit=period + 2)
        return atr_indicator(candles, period)

    def _stop_distance(self, view: _MarketView, reference: float) -> float:
        """Stop distance: ATR × multiple, con respaldo del 0.5% si no hay ATR."""
        if view.atr and view.atr > 0:
            return view.atr * self._settings.sizing.atr_stop_multiplier
        return reference * 0.005

    def _stops(self, side: OrderSide, reference: float, distance: float) -> tuple[float, float]:
        """Compute (stop_loss, take_profit) around the entry reference."""
        rr = self._settings.sizing.reward_risk
        if side is OrderSide.BUY:
            return round(reference - distance, 8), round(reference + distance * rr, 8)
        return round(reference + distance, 8), round(reference - distance * rr, 8)

    @staticmethod
    def _mark_price(position: Position, ticker: Ticker) -> float:
        """Side-appropriate exit price used to mark and to check exits."""
        return ticker.bid if position.side is PositionSide.LONG else ticker.ask

    def _slip_context(self, view: _MarketView, quantity: float) -> SlippageContext:
        """Build the slippage context for a fill."""
        return SlippageContext(
            atr_pct=view.atr_pct,
            spread_bps=view.spread_bps,
            available_liquidity=view.volume or 0.0,
            order_quantity=quantity,
            session=view.session,
            order_type=OrderType.MARKET,
        )

    def _symbol_exposures(self) -> dict[str, float]:
        """Current nominal exposure per symbol with open positions."""
        exposures: dict[str, float] = {}
        for position in self._positions.open_positions:
            exposures[position.symbol] = exposures.get(position.symbol, 0.0) + position.notional
        return exposures

    def _perf_inputs(self) -> tuple[float | None, float | None]:
        """Win rate and reward:risk from the journal (for Kelly sizing)."""
        if self._settings.sizing.method != "kelly":
            return None, None
        report = self._performance.compute(self._journal.all())
        if report.total_trades < 10:
            return None, None
        return report.win_rate, report.risk_reward

    def _trade_record(self, position: Position, exit_fill: Fill) -> TradeRecord:
        """Build the journal record from a just-closed position."""
        entry_slip = float(position.metadata.get("entry_slippage_bps", 0.0) or 0.0)
        avg_slip = (entry_slip + exit_fill.slippage_bps) / 2.0
        gross = position.realized_pnl + position.commission_paid
        return TradeRecord(
            position_id=position.position_id,
            symbol=position.symbol,
            side=position.side,
            quantity=position.initial_quantity,
            entry_time=position.opened_at,
            exit_time=position.closed_at or utc_now(),
            entry_price=position.entry_price,
            exit_price=exit_fill.price,
            stop_loss=position.initial_stop,
            take_profit=position.take_profit,
            commission=position.commission_paid,
            slippage_bps=round(avg_slip, 4),
            spread_bps=exit_fill.spread_bps,
            pnl=round(position.realized_pnl, 6),
            pnl_gross=round(gross, 6),
            r_multiple=round(position.r_multiple(exit_fill.price), 4),
            return_pct=round(
                position.realized_pnl / position.cost_basis * 100.0 if position.cost_basis else 0.0,
                4,
            ),
            atr=position.metadata.get("atr"),
            volatility=str(position.metadata.get("volatility", "normal")),
            regime=position.regime,
            score=position.score,
            confidence=position.confidence,
            exit_reason=position.exit_reason or ExitReason.MANUAL,
            entry_reasons=position.entry_reasons,
            exit_reasons=position.exit_reasons,
            decision_id=position.decision_id,
        )

    # ------------------------------------------------------------------
    # Publicación de eventos
    # ------------------------------------------------------------------

    async def _reject(
        self, symbol: str, side: OrderSide, reason: RejectReason, rule: str, detail: str
    ) -> None:
        """Publish an order rejection plus its risk explanation."""
        self._log.warning(
            "Order rejected %s %s — %s: %s (%s)",
            symbol, side.value, rule, detail, reason.value,
        )
        request = OrderRequest(symbol=symbol, side=side, quantity=0.0, reason=detail)
        order = self._orders.create(request)
        self._orders.reject(order, reason)
        await self._publish(
            ev.OrderRejected(
                source="execution_engine",
                order_id=order.order_id,
                symbol=symbol,
                side=side.value,
                reason=f"{rule}: {detail}" if rule else reason.value,
            )
        )
        await self._publish(
            ev.RiskTriggered(
                source="execution_engine", rule=rule or reason.value, symbol=symbol, detail=detail
            )
        )

    async def _announce_fill(self, order: Order, fill: Fill) -> None:
        """Publish an order execution event."""
        await self._publish(
            ev.OrderExecuted(
                source="execution_engine",
                order_id=order.order_id,
                symbol=fill.symbol,
                side=fill.side.value,
                quantity=fill.quantity,
                price=fill.price,
                commission=fill.commission,
                slippage_bps=fill.slippage_bps,
                partial=fill.is_partial,
            )
        )

    async def _announce_stop(self, position: Position, update: object) -> None:
        """Publish break-even/trailing/stop-moved events for a stop update."""
        from app.execution.position_manager import StopUpdate

        if not isinstance(update, StopUpdate):
            return
        if update.kind == "break_even":
            await self._publish(
                ev.BreakEvenActivated(
                    source="execution_engine",
                    position_id=position.position_id,
                    symbol=position.symbol,
                    price=update.current,
                )
            )
        elif update.kind == "trailing":
            await self._publish(
                ev.TrailingUpdated(
                    source="execution_engine",
                    position_id=position.position_id,
                    symbol=position.symbol,
                    stop_loss=update.current,
                )
            )
        await self._publish(
            ev.StopMoved(
                source="execution_engine",
                position_id=position.position_id,
                symbol=position.symbol,
                previous=update.previous,
                current=update.current,
                reason=update.kind,
            )
        )

    async def _publish(self, event: Event) -> None:
        """Publish an event tolerating a stopped/saturated bus."""
        if self._bus is None:
            return
        with contextlib.suppress(Exception):
            await self._bus.publish(event)

    # ------------------------------------------------------------------
    # Diagnóstico
    # ------------------------------------------------------------------

    def status(self) -> dict[str, object]:
        """Full execution status for the dashboard."""
        return {
            "mode": self._settings.resolved_mode(),
            "enabled": self._settings.enabled,
            "portfolio": self._portfolio.status(),
            "positions": self._positions.status(),
            "orders": self._orders.status(),
            "risk": self._risk.status(),
            "paper": self._paper.stats,
            "journal": self._journal.status(),
        }


def _session_for(hour: int) -> str:
    """Return the first UTC session matching the hour (``off`` if none)."""
    for name, (start, end) in _SESSION_HOURS.items():
        if start <= hour < end:
            return name
    return "off"


def _fmt(value: float | None) -> str:
    """Format an optional price for logs."""
    return "—" if value is None else f"{value:.4f}"
