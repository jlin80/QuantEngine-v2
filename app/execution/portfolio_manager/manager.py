"""Portfolio Manager: contabilidad de caja, equity, exposición y drawdown.

Modelo de margen (soporta largos y cortos por igual):
    * ``balance`` es la caja realizada: sólo cambia por comisiones y por el
      PnL bruto de posiciones cerradas.
    * ``equity`` = balance + PnL flotante de las posiciones abiertas.
    * ``used_capital`` = suma de nocionales de entrada / apalancamiento.
    * ``drawdown`` se mide contra el equity pico histórico.

El Portfolio Manager no posee las posiciones (eso es del Position Manager):
recibe la lista de abiertas para valorar la cartera bajo demanda.
"""

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from app.execution.models import PortfolioSnapshot, Position
from app.utils.time import utc_now

_log = logging.getLogger("app.execution.portfolio")


class PortfolioManager:
    """Owns cash accounting and portfolio-level metrics.

    Args:
        initial_balance: Balance inicial de la cuenta (paper).
        base_currency: Divisa base.
        leverage: Apalancamiento nominal (1.0 = sin apalancamiento).
        peak_path: Fichero donde persistir el **máximo histórico de equity**.
            Con ``None`` (backtests, tests) no se persiste nada.
    """

    def __init__(
        self,
        initial_balance: float,
        *,
        base_currency: str = "USD",
        leverage: float = 1.0,
        peak_path: Path | None = None,
    ) -> None:
        self._initial_balance = initial_balance
        self._balance = initial_balance
        self._base_currency = base_currency
        self._leverage = max(1.0, leverage)
        self._peak_path = peak_path
        self._stored_peak = self._load_peak()
        self._peak_equity = max(initial_balance, self._stored_peak)
        self._total_trades = 0
        self._wins = 0
        self._losses = 0
        self._commission_paid = 0.0
        self._last_floating = 0.0

    def _load_peak(self) -> float:
        """Read the stored high-water mark (0.0 if there is none or it is unreadable).

        Un fichero ilegible no puede tumbar el arranque del motor: se avisa y se
        sigue con 0.0, que es el comportamiento anterior a persistir nada.
        """
        if self._peak_path is None or not self._peak_path.exists():
            return 0.0
        try:
            data = json.loads(self._peak_path.read_text(encoding="utf-8"))
            return float(data.get("peak_equity", 0.0))
        except (OSError, ValueError, TypeError) as exc:
            _log.warning("No se pudo leer el pico de equity (%s); se arranca sin el", exc)
            return 0.0

    def _remember_peak(self) -> None:
        """Persist the high-water mark when it grows.

        **Un depósito no borra el pico anterior.** Es deliberado: si al ingresar
        dinero se reiniciara la referencia, bastaría recargar la cuenta para que
        un drawdown desapareciera de la vista, y el freno dejaria de ver
        justamente la situación en la que más importa.
        """
        if self._peak_path is None or self._peak_equity <= self._stored_peak:
            return
        self._stored_peak = self._peak_equity
        try:
            self._peak_path.parent.mkdir(parents=True, exist_ok=True)
            self._peak_path.write_text(
                json.dumps({"peak_equity": round(self._peak_equity, 4)}), encoding="utf-8"
            )
        except OSError as exc:
            _log.warning("No se pudo guardar el pico de equity (%s)", exc)

    def restore(
        self,
        *,
        balance: float,
        peak_equity: float,
        total_trades: int,
        wins: int,
        losses: int,
        commission_paid: float,
    ) -> None:
        """Re-adopt the accounting state recovered after a restart (Fase 9).

        ``peak_equity`` importa especialmente: es la base del drawdown y, por
        tanto, del kill switch. Arrancar de cero tras un reinicio borraría el
        pico histórico y con él la memoria de cuánto se lleva perdido.

        Args:
            balance: Saldo realizado.
            peak_equity: Pico de equity histórico.
            total_trades: Operaciones cerradas.
            wins: Operaciones ganadoras.
            losses: Operaciones perdedoras.
            commission_paid: Comisiones acumuladas.
        """
        self._balance = balance
        self._peak_equity = max(peak_equity, balance, self._stored_peak)
        self._remember_peak()
        self._total_trades = total_trades
        self._wins = wins
        self._losses = losses
        self._commission_paid = commission_paid

    def sync_from_broker(self, balance: float, *, set_baseline: bool = False) -> None:
        """Adopt the real broker cash balance (demo: ejecución real).

        En demo las órdenes van al broker real, así que el saldo realizado debe
        reflejar la cuenta MT5 y no una caja simulada. La primera sincronización
        fija además la línea base (``set_baseline``) para que el return y el
        drawdown se midan desde el arranque del bot, no desde un valor de config.

        Args:
            balance: Balance realizado real reportado por el broker.
            set_baseline: Si además fija el balance inicial y el pico de equity
                (sólo la primera vez).
        """
        self._balance = balance
        if set_baseline:
            self._initial_balance = balance
            # El pico NO se reinicia con el arranque. Antes sí, y por eso el
            # drawdown era ciego entre reinicios: la cuenta demo llegó al 52.5 %
            # el 2026-08-25, la máquina reinició, y el motor volvió marcando
            # 0 %. La herramienta que lee el journal veía 67.4 % al mismo tiempo
            # que la API en vivo decía 6.86 %. Un freno cuya memoria se borra en
            # cada arranque no es un freno — y esta se borraba también en cada
            # despliegue.
            self._peak_equity = max(balance, self._stored_peak)
        else:
            self._peak_equity = max(self._peak_equity, balance)
        self._remember_peak()

    @property
    def balance(self) -> float:
        """Realized cash balance."""
        return self._balance

    @property
    def initial_balance(self) -> float:
        """Starting balance."""
        return self._initial_balance

    @property
    def realized_pnl(self) -> float:
        """Realized PnL since inception (net of every commission)."""
        return self._balance - self._initial_balance

    @property
    def total_trades(self) -> int:
        """Number of closed trades."""
        return self._total_trades

    @property
    def wins(self) -> int:
        """Number of winning trades."""
        return self._wins

    @property
    def losses(self) -> int:
        """Number of losing trades."""
        return self._losses

    @property
    def commission_paid(self) -> float:
        """Accumulated commission."""
        return self._commission_paid

    @property
    def peak_equity(self) -> float:
        """Running equity peak (drawdown baseline)."""
        return self._peak_equity

    def on_open_commission(self, commission: float) -> None:
        """Charge the opening commission to the cash balance."""
        self._balance -= commission
        self._commission_paid += commission

    def on_trade_closed(self, *, gross_pnl: float, close_commission: float) -> None:
        """Settle a closed trade into the cash balance.

        Args:
            gross_pnl: PnL bruto del cierre (sin comisiones).
            close_commission: Comisión del cierre.
        """
        self._balance += gross_pnl - close_commission
        self._commission_paid += close_commission
        net = gross_pnl - close_commission
        self._total_trades += 1
        if net > 0:
            self._wins += 1
        elif net < 0:
            self._losses += 1

    def floating_pnl(self, positions: Iterable[Position]) -> float:
        """Aggregate unrealized PnL of the open positions."""
        return sum(pos.unrealized_pnl() for pos in positions)

    def used_capital(self, positions: Iterable[Position]) -> float:
        """Capital committed as margin across open positions."""
        return sum(pos.cost_basis for pos in positions) / self._leverage

    def exposure(self, positions: Iterable[Position]) -> float:
        """Total nominal exposure at mark prices."""
        return sum(pos.notional for pos in positions)

    def equity(self, positions: Iterable[Position]) -> float:
        """Equity = cash balance + floating PnL."""
        return self._balance + self.floating_pnl(positions)

    def snapshot(self, positions: Iterable[Position]) -> PortfolioSnapshot:
        """Build a full portfolio snapshot and update the equity peak.

        Args:
            positions: Posiciones abiertas actuales.

        Returns:
            Instantánea completa (JSON-safe) del portafolio.
        """
        open_positions = list(positions)
        floating = self.floating_pnl(open_positions)
        self._last_floating = floating
        equity = self._balance + floating
        used = self.used_capital(open_positions)
        exposure = self.exposure(open_positions)
        self._peak_equity = max(self._peak_equity, equity)
        self._remember_peak()
        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = max(0.0, (self._peak_equity - equity) / self._peak_equity * 100.0)
        exposure_pct = (exposure / equity * 100.0) if equity > 0 else 0.0
        return PortfolioSnapshot(
            timestamp=utc_now(),
            initial_balance=self._initial_balance,
            balance=round(self._balance, 6),
            equity=round(equity, 6),
            floating_pnl=round(floating, 6),
            realized_pnl=round(self.realized_pnl, 6),
            used_capital=round(used, 6),
            free_capital=round(equity - used, 6),
            exposure=round(exposure, 6),
            exposure_pct=round(exposure_pct, 4),
            peak_equity=round(self._peak_equity, 6),
            drawdown_pct=round(drawdown_pct, 4),
            open_positions=len(open_positions),
            total_trades=self._total_trades,
            base_currency=self._base_currency,
        )

    def status(self) -> dict[str, object]:
        """Compact accounting status (sin necesitar posiciones)."""
        return {
            "initial_balance": self._initial_balance,
            "balance": round(self._balance, 6),
            "realized_pnl": round(self.realized_pnl, 6),
            "peak_equity": round(self._peak_equity, 6),
            "total_trades": self._total_trades,
            "wins": self._wins,
            "losses": self._losses,
            "commission_paid": round(self._commission_paid, 6),
            "base_currency": self._base_currency,
            "leverage": self._leverage,
        }
