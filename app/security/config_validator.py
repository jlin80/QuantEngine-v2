"""Validación de configuración para producción (Fase 9).

Antes de operar 24/7 conviene detectar configuraciones peligrosas o incompletas:
secretos vacíos en producción, notificaciones activadas sin webhook, live
solicitado sin la maquinaria completa. No lanza excepciones —devuelve una lista
de hallazgos con severidad— para que el dashboard y el arranque decidan qué
hacer. La regla de oro sigue vigente: ante configuración dudosa, no operar live.
"""

from dataclasses import dataclass
from typing import Any

from app.config.environment import Environment
from app.config.settings import Settings


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    """One configuration finding.

    Attributes:
        severity: ``error`` (bloquea producción) o ``warning`` (revisar).
        field: Dotted config path involved.
        message: Human-readable explanation.
    """

    severity: str
    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """JSON-safe dict."""
        return {"severity": self.severity, "field": self.field, "message": self.message}


def validate_config(settings: Settings) -> list[ConfigIssue]:
    """Validate settings for production readiness.

    Args:
        settings: Root settings.

    Returns:
        Findings, most severe first (empty when nothing is wrong).
    """
    issues: list[ConfigIssue] = []
    is_prod = settings.environment is Environment.PRODUCTION

    # Discord es el único medio de notificación: si está activo, necesita webhook.
    if settings.discord.enabled and not settings.discord.webhook_url.get_secret_value():
        issues.append(
            ConfigIssue(
                "warning" if not is_prod else "error",
                "discord.webhook_url",
                "Discord habilitado sin webhook: no se entregará ninguna notificación.",
            )
        )

    # Secretos críticos en producción.
    if is_prod and settings.security.require_secrets_in_production:
        if not settings.database.password.get_secret_value():
            issues.append(
                ConfigIssue("error", "database.password", "Contraseña de base de datos vacía.")
            )
        if settings.production.allow_live and not settings.broker.api_key.get_secret_value():
            issues.append(
                ConfigIssue("error", "broker.api_key", "allow_live con broker sin credenciales.")
            )

    # Notion habilitado sin credenciales.
    if settings.notion.enabled and (
        not settings.notion.api_key.get_secret_value() or not settings.notion.database_id
    ):
        issues.append(
            ConfigIssue(
                "warning",
                "notion",
                "Notion habilitado sin api_key/database_id: la cola crecerá sin entregarse.",
            )
        )

    # Coherencia de live: pedir live sin la capa de producción es una trampa.
    if settings.execution.mode == "live" and not settings.production.enabled:
        issues.append(
            ConfigIssue(
                "error",
                "execution.mode",
                "execution.mode=live sin production.enabled: el Live Gate no existe.",
            )
        )
    if settings.production.allow_live and not is_prod:
        issues.append(
            ConfigIssue(
                "warning",
                "production.allow_live",
                "allow_live fuera de producción: el gate exige environment=production igualmente.",
            )
        )

    order = {"error": 0, "warning": 1}
    return sorted(issues, key=lambda i: order.get(i.severity, 2))


def validation_report(settings: Settings) -> dict[str, Any]:
    """Full validation report for the dashboard/API."""
    issues = validate_config(settings)
    errors = [i for i in issues if i.severity == "error"]
    return {
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(issues) - len(errors),
        "issues": [issue.to_dict() for issue in issues],
    }
