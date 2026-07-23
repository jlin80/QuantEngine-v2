"""Live Gate: la única puerta hacia Live Trading, y no tiene atajos.

Filosofía, heredada del Decision Engine y del Strategy Qualification Pipeline:
**nunca se responde "no" a secas**. El gate devuelve criterio por criterio qué
se cumple y qué no, para que el operador vea exactamente qué falta.

Dos decisiones de diseño que conviene entender antes de tocar este módulo:

1. **Fail-closed.** Un criterio cuyo valor es desconocido (``None``) cuenta como
   fallido. En un gate de seguridad, "no lo sé" y "no" son lo mismo.
2. **El hash cierra el círculo.** El reporte se identifica por un hash de los
   criterios configurados más el resultado de cada comprobación. La aprobación
   del operador se firma contra ese hash, así que relajar un umbral después de
   aprobar invalida la aprobación en vez de heredarla.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import LiveGateSettings
from app.utils.time import utc_now


@dataclass(frozen=True, slots=True)
class GateCheck:
    """One criterion evaluated by the gate.

    Attributes:
        name: Stable criterion identifier.
        passed: Whether the criterion is satisfied.
        detail: Human-readable explanation (always filled, pass or fail).
        blocking: Whether failing it blocks live trading.
    """

    name: str
    passed: bool
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class GateInputs:
    """Live state the gate evaluates.

    Everything is optional because the gate must work with a partially wired
    system — but ``None`` means "unknown", and unknown fails. The caller
    (:class:`~app.production.api.ProductionAPI`) fills what it can observe.
    """

    allow_live: bool = False
    mode_requested: str = "paper"
    environment: str = "development"
    # Historial de paper trading.
    paper_trades: int = 0
    paper_days: float = 0.0
    profit_factor: float | None = None
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    # Validación científica (Fase 6).
    qualification_approved: bool | None = None
    walk_forward_approved: bool | None = None
    monte_carlo_approved: bool | None = None
    # Machine Learning (Fase 7).
    ml_stable: bool | None = None
    critical_drift: bool | None = None
    # Calidad e incidencias.
    test_coverage_pct: float | None = None
    open_critical_errors: int = 0
    # Infraestructura viva.
    system_healthy: bool | None = None
    broker_connected: bool | None = None
    discord_ok: bool | None = None
    database_ok: bool | None = None
    redis_ok: bool | None = None
    watchdog_ok: bool | None = None
    scheduler_ok: bool | None = None
    risk_manager_ok: bool | None = None
    # Estado operativo que veta por sí solo.
    kill_switch_active: bool = False
    safe_mode_active: bool = False


@dataclass(frozen=True, slots=True)
class LiveGateReport:
    """Full outcome of a gate evaluation.

    Attributes:
        approved: Whether every blocking criterion passed.
        checks: Every criterion evaluated, in evaluation order.
        reasons: Human-readable blocking reasons (empty when approved).
        report_hash: Identity of this evaluation, used to bind approvals.
        evaluated_at: UTC moment of the evaluation.
    """

    approved: bool
    checks: list[GateCheck]
    reasons: list[str]
    report_hash: str
    evaluated_at: datetime = field(default_factory=utc_now)

    @property
    def passed_count(self) -> int:
        """Number of satisfied criteria."""
        return sum(1 for check in self.checks if check.passed)

    @property
    def failed_count(self) -> int:
        """Number of unsatisfied criteria."""
        return sum(1 for check in self.checks if not check.passed)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "approved": self.approved,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "reasons": list(self.reasons),
            "checks": [check.to_dict() for check in self.checks],
            "report_hash": self.report_hash,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class LiveGate:
    """Evaluate every mandatory criterion before live trading may be enabled.

    Args:
        settings: Configured thresholds and which criteria are required.
        approval_lookup: Callback answering whether a valid operator approval
            exists for a given report hash. Injected so the gate stays pure and
            testable.
    """

    def __init__(
        self,
        settings: LiveGateSettings,
        approval_lookup: Callable[[str], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._approval_lookup = approval_lookup or (lambda _hash: False)
        self._last_report: LiveGateReport | None = None

    @property
    def last_report(self) -> LiveGateReport | None:
        """Most recent evaluation, or ``None`` if never evaluated."""
        return self._last_report

    def evaluate(self, inputs: GateInputs) -> LiveGateReport:
        """Evaluate every criterion and produce an explained report.

        Args:
            inputs: Observed state of the system.

        Returns:
            The report. ``approved`` is ``True`` only when every blocking
            criterion passes, including the operator approval.
        """
        checks = self._core_checks(inputs)
        report_hash = self._hash(checks)
        # La aprobación se comprueba al final, contra el hash de todo lo demás:
        # así aprobar un reporte no aprueba un sistema distinto.
        if self._settings.require_operator_approval:
            approved_by_operator = self._approval_lookup(report_hash)
            checks.append(
                GateCheck(
                    "operator_approval",
                    approved_by_operator,
                    (
                        "Aprobación explícita del operador vigente"
                        if approved_by_operator
                        else "Falta la aprobación explícita del operador para este reporte"
                    ),
                )
            )
        reasons = [check.detail for check in checks if not check.passed and check.blocking]
        report = LiveGateReport(
            approved=not reasons,
            checks=checks,
            reasons=reasons,
            report_hash=report_hash,
        )
        self._last_report = report
        return report

    # ------------------------------------------------------------------
    # Criterios
    # ------------------------------------------------------------------

    def _core_checks(self, inputs: GateInputs) -> list[GateCheck]:
        """Every criterion except the operator approval."""
        checks: list[GateCheck] = [
            GateCheck(
                "allow_live",
                inputs.allow_live,
                (
                    "Llave maestra production.allow_live activada"
                    if inputs.allow_live
                    else "production.allow_live está en False (llave maestra cerrada)"
                ),
            ),
            GateCheck(
                "mode_requested",
                inputs.mode_requested == "live",
                f"execution.mode = {inputs.mode_requested!r}"
                + ("" if inputs.mode_requested == "live" else " (no se pide live)"),
            ),
            GateCheck(
                "environment",
                inputs.environment == "production",
                f"Ambiente {inputs.environment!r}"
                + ("" if inputs.environment == "production" else " (live exige 'production')"),
            ),
            GateCheck(
                "kill_switch",
                not inputs.kill_switch_active,
                ("Kill switch activo" if inputs.kill_switch_active else "Kill switch liberado"),
            ),
            GateCheck(
                "safe_mode",
                not inputs.safe_mode_active,
                "Safe Mode activo" if inputs.safe_mode_active else "Fuera de Safe Mode",
            ),
        ]
        checks.extend(self._track_record_checks(inputs))
        checks.extend(self._validation_checks(inputs))
        checks.extend(self._quality_checks(inputs))
        checks.extend(self._infrastructure_checks(inputs))
        return checks

    def _track_record_checks(self, inputs: GateInputs) -> list[GateCheck]:
        """Statistical criteria over the paper trading track record."""
        s = self._settings
        return [
            _at_least(
                "paper_trades", float(inputs.paper_trades), float(s.min_paper_trades), " ops"
            ),
            _at_least("paper_days", inputs.paper_days, s.min_paper_days, " días"),
            _at_least("profit_factor", inputs.profit_factor, s.min_profit_factor, ""),
            _at_least("sharpe", inputs.sharpe, s.min_sharpe, ""),
            _at_most("max_drawdown_pct", inputs.max_drawdown_pct, s.max_drawdown_pct, "%"),
        ]

    def _validation_checks(self, inputs: GateInputs) -> list[GateCheck]:
        """Scientific validation criteria (Fase 6) and ML stability (Fase 7)."""
        s = self._settings
        checks: list[GateCheck] = []
        if s.require_qualification:
            checks.append(
                _required_flag(
                    "qualification",
                    inputs.qualification_approved,
                    "Strategy Qualification Pipeline",
                )
            )
        if s.require_walk_forward:
            checks.append(
                _required_flag("walk_forward", inputs.walk_forward_approved, "Walk Forward")
            )
        if s.require_monte_carlo:
            checks.append(_required_flag("monte_carlo", inputs.monte_carlo_approved, "Monte Carlo"))
        if s.require_ml_stable:
            checks.append(_required_flag("ml_stable", inputs.ml_stable, "Machine Learning estable"))
        if s.require_no_critical_drift:
            drift = inputs.critical_drift
            checks.append(
                GateCheck(
                    "no_critical_drift",
                    drift is False,
                    (
                        "Sin drift crítico"
                        if drift is False
                        else (
                            "Drift crítico detectado"
                            if drift is True
                            else "Estado de drift desconocido (se asume crítico)"
                        )
                    ),
                )
            )
        return checks

    def _quality_checks(self, inputs: GateInputs) -> list[GateCheck]:
        """Test coverage and open critical incidents."""
        s = self._settings
        return [
            _at_least("test_coverage_pct", inputs.test_coverage_pct, s.min_test_coverage_pct, "%"),
            GateCheck(
                "no_critical_errors",
                inputs.open_critical_errors <= s.max_open_critical_errors,
                f"{inputs.open_critical_errors} errores críticos abiertos "
                f"(máximo {s.max_open_critical_errors})",
            ),
        ]

    def _infrastructure_checks(self, inputs: GateInputs) -> list[GateCheck]:
        """Live infrastructure criteria."""
        s = self._settings
        wanted: list[tuple[bool, str, bool | None, str]] = [
            (
                s.require_healthy_system,
                "system_health",
                inputs.system_healthy,
                "Health del sistema",
            ),
            (s.require_broker_connected, "broker", inputs.broker_connected, "Broker conectado"),
            (s.require_discord, "discord", inputs.discord_ok, "Discord operativo"),
            (s.require_database, "database", inputs.database_ok, "Base de datos disponible"),
            (s.require_redis, "redis", inputs.redis_ok, "Redis disponible"),
            (s.require_watchdog, "watchdog", inputs.watchdog_ok, "Watchdog activo"),
            (s.require_scheduler, "scheduler", inputs.scheduler_ok, "Scheduler activo"),
            (s.require_risk_manager, "risk_manager", inputs.risk_manager_ok, "Risk Manager activo"),
        ]
        return [
            _required_flag(name, value, label)
            for required, name, value, label in wanted
            if required
        ]

    def _hash(self, checks: list[GateCheck]) -> str:
        """Identity of an evaluation: configured criteria + every check outcome."""
        payload = {
            "criteria": self._settings.model_dump(mode="json"),
            "checks": [(check.name, check.passed) for check in checks],
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# Constructores de criterios (fail-closed ante ``None``)
# ----------------------------------------------------------------------


def _at_least(name: str, value: float | None, minimum: float, unit: str) -> GateCheck:
    """Criterion satisfied when ``value >= minimum``; unknown values fail."""
    if value is None:
        return GateCheck(name, False, f"{name} desconocido (mínimo {minimum:g}{unit})")
    passed = value >= minimum
    return GateCheck(name, passed, f"{name} {value:g}{unit} (mínimo {minimum:g}{unit})")


def _at_most(name: str, value: float | None, maximum: float, unit: str) -> GateCheck:
    """Criterion satisfied when ``value <= maximum``; unknown values fail."""
    if value is None:
        return GateCheck(name, False, f"{name} desconocido (máximo {maximum:g}{unit})")
    passed = value <= maximum
    return GateCheck(name, passed, f"{name} {value:g}{unit} (máximo {maximum:g}{unit})")


def _required_flag(name: str, value: bool | None, label: str) -> GateCheck:
    """Criterion satisfied only when the flag is explicitly ``True``."""
    if value is None:
        return GateCheck(name, False, f"{label}: estado desconocido (se asume no cumplido)")
    return GateCheck(name, value, f"{label}: {'OK' if value else 'NO disponible'}")
