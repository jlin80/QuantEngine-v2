"""Notificaciones del laboratorio por Discord (Fase 6).

Servicio desacoplado que traduce los hitos del backtesting (inicio/fin de
backtest, mejor resultado, optimización, walk-forward, Monte Carlo, aprobación o
rechazo de una estrategia, errores críticos) a embeds de Discord. Reutiliza el
único canal de notificaciones del proyecto: el ``NotificationService`` de la
Fase 1. Nunca formatea webhooks a mano ni abre otros canales.
"""

from typing import Any

from app.notifications import NotificationLevel, NotificationService

_SOURCE = "backtesting"


class BacktestNotifier:
    """Send backtesting milestones to Discord via the notification service.

    Args:
        notifications: Servicio de notificaciones (canal Discord).
    """

    def __init__(self, notifications: NotificationService) -> None:
        self._notifications = notifications

    async def backtest_started(self, label: str, symbol: str, bars: int) -> None:
        """Announce the start of a backtest."""
        await self._notifications.send(
            "Backtest iniciado",
            f"Comienza el backtest **{label}**.",
            level=NotificationLevel.INFO,
            fields={"Símbolo": symbol, "Velas": str(bars)},
            source=_SOURCE,
        )

    async def backtest_finished(self, label: str, statistics: dict[str, Any]) -> None:
        """Announce the end of a backtest with its headline metrics."""
        await self._notifications.send(
            "Backtest finalizado",
            f"Terminó el backtest **{label}**.",
            level=NotificationLevel.SUCCESS,
            fields=_metric_fields(statistics),
            source=_SOURCE,
        )

    async def optimization_finished(
        self, label: str, method: str, best_score: float, best_params: dict[str, Any]
    ) -> None:
        """Announce the best result found by an optimization."""
        await self._notifications.send(
            "Optimización completada",
            f"Mejor resultado de **{label}** ({method}).",
            level=NotificationLevel.SUCCESS,
            fields={
                "Score": f"{best_score:.4f}",
                "Parámetros": ", ".join(f"{k}={v}" for k, v in best_params.items()) or "—",
            },
            source=_SOURCE,
        )

    async def walk_forward_finished(self, label: str, report: dict[str, Any]) -> None:
        """Announce the end of a walk-forward analysis."""
        await self._notifications.send(
            "Walk Forward finalizado",
            f"Análisis walk-forward de **{label}**.",
            level=NotificationLevel.INFO,
            fields={
                "Retorno OOS medio": f"{report.get('average_out_of_sample_return_pct', 0.0)}%",
                "Pliegues positivos": f"{report.get('positive_fold_ratio', 0.0):.0%}",
                "Estable": str(report.get("is_stable", False)),
            },
            source=_SOURCE,
        )

    async def monte_carlo_finished(self, label: str, report: dict[str, Any]) -> None:
        """Announce the end of a Monte Carlo study."""
        await self._notifications.send(
            "Monte Carlo finalizado",
            f"Simulación Monte Carlo de **{label}**.",
            level=NotificationLevel.INFO,
            fields={
                "Retorno medio": f"{report.get('mean_return_pct', 0.0)}%",
                "Riesgo de ruina": f"{report.get('risk_of_ruin', 0.0):.2%}",
                "Drawdown esperado": f"{report.get('expected_max_drawdown_pct', 0.0)}%",
            },
            source=_SOURCE,
        )

    async def strategy_qualified(self, strategy: str, approved: bool, reasons: list[str]) -> None:
        """Announce whether a strategy passed the qualification pipeline."""
        if approved:
            await self._notifications.send(
                "Nueva estrategia supera el laboratorio",
                f"**{strategy}** aprobada para paper trading.",
                level=NotificationLevel.SUCCESS,
                fields={"Motivos": " · ".join(reasons[:5]) or "cumple todos los mínimos"},
                source=_SOURCE,
            )
        else:
            await self._notifications.send(
                "Estrategia rechazada",
                f"**{strategy}** no supera la validación.",
                level=NotificationLevel.WARNING,
                fields={"Motivos": " · ".join(reasons[:5]) or "no cumple los mínimos"},
                source=_SOURCE,
            )

    async def critical_error(self, label: str, detail: str) -> None:
        """Announce a critical error during a laboratory run."""
        await self._notifications.send(
            "Error crítico en el laboratorio",
            f"Fallo durante **{label}**.",
            level=NotificationLevel.CRITICAL,
            fields={"Detalle": detail[:512]},
            source=_SOURCE,
        )


def _metric_fields(statistics: dict[str, Any]) -> dict[str, str]:
    """Pick the headline metrics for a Discord embed."""
    keys = ("total_trades", "profit_factor", "sharpe", "sqn", "max_drawdown_pct")
    return {key: str(statistics.get(key, "—")) for key in keys}
