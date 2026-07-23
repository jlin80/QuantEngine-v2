"""ExecutionCore: fachada de las APIs internas de la capa de ejecución.

Punto único de entrada para el dashboard y para el resto del sistema: estado
del portafolio, posiciones, historial, riesgo, rendimiento y reportes. Refleja
el patrón de ``QuantCore`` en el núcleo cuantitativo.
"""

from typing import Any

from app.execution.execution_engine import ExecutionEngine
from app.execution.journal import TradeJournal
from app.execution.notifications import ExecutionNotifier
from app.execution.performance import PerformanceEngine
from app.execution.portfolio_manager import PortfolioManager
from app.execution.position_manager import PositionManager
from app.execution.risk_manager import RiskManager


class ExecutionCore:
    """Internal API surface of the execution layer.

    Args:
        engine: Execution Engine (orquestador).
        positions: Position Manager.
        portfolio: Portfolio Manager.
        risk: Risk Manager.
        journal: Trade Journal.
        performance: Performance Engine.
        notifier: Servicio de notificaciones de ejecución (opcional).
    """

    def __init__(
        self,
        *,
        engine: ExecutionEngine,
        positions: PositionManager,
        portfolio: PortfolioManager,
        risk: RiskManager,
        journal: TradeJournal,
        performance: PerformanceEngine,
        notifier: ExecutionNotifier | None = None,
    ) -> None:
        self.engine = engine
        self.positions = positions
        self.portfolio = portfolio
        self.risk = risk
        self.journal = journal
        self.performance = performance
        self.notifier = notifier

    # ------------------------------------------------------------------
    # Lecturas para el dashboard
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Full execution status."""
        return self.engine.status()

    def portfolio_snapshot(self) -> dict[str, Any]:
        """Current portfolio snapshot."""
        return self.portfolio.snapshot(self.positions.open_positions).to_dict()

    def open_positions(self) -> list[dict[str, Any]]:
        """Currently open positions."""
        return [p.to_dict() for p in self.positions.open_positions]

    def closed_positions(self, limit: int = 100) -> list[dict[str, Any]]:
        """Recently closed positions."""
        return [p.to_dict() for p in self.positions.closed_positions[-limit:]]

    def trades(self, limit: int = 100) -> list[dict[str, Any]]:
        """Recent journalled trades."""
        return [t.to_dict() for t in self.journal.recent(limit)]

    def performance_report(self) -> dict[str, Any]:
        """Performance metrics over the whole journal."""
        return self.performance.compute(self.journal.all()).to_dict()

    def risk_status(self) -> dict[str, Any]:
        """Risk Manager status (limits, kill switch, circuit breaker)."""
        return self.risk.status()

    def recent_notifications(self, limit: int = 20) -> list[dict[str, str]]:
        """Last notifications delivered by the execution notifier."""
        return self.notifier.recent(limit) if self.notifier is not None else []

    def generate_trade_report(self) -> dict[str, Any]:
        """Combined portfolio + performance report (JSON-safe)."""
        return {
            "portfolio": self.portfolio_snapshot(),
            "performance": self.performance_report(),
            "risk": self.risk_status(),
        }

    async def send_periodic_report(self, title: str) -> None:
        """Deliver a periodic report to Discord (used by the scheduler)."""
        if self.notifier is None:
            return
        await self.notifier.send_report(title, self.portfolio_snapshot(), self.performance_report())
