"""Taxonomía de acciones auditables.

El spec de la Fase 9 enumera qué debe quedar registrado. Tenerlo como enum en
vez de cadenas sueltas evita que dos módulos escriban la misma acción con
nombres distintos y hace que el dashboard pueda filtrar por categoría.

Nunca se elimina una auditoría: este enum sólo crece.
"""

import enum


class AuditAction(enum.StrEnum):
    """Acciones que el sistema registra de forma permanente."""

    # Ciclo de vida del sistema.
    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"
    SYSTEM_RESTARTED = "system.restarted"
    SYSTEM_RECONNECTED = "system.reconnected"
    SYSTEM_RECOVERED = "system.recovered"

    # Configuración y riesgo.
    CONFIG_PATCH = "config.patch"
    RISK_CHANGED = "risk.changed"

    # Estrategias.
    STRATEGY_ENABLED = "strategy.enabled"
    STRATEGY_DISABLED = "strategy.disabled"
    STRATEGY_WEIGHT_CHANGED = "strategy.weight_changed"

    # Machine Learning y backtesting.
    ML_TRAINING = "ml.training"
    ML_MODEL_ACTIVATED = "ml.model_activated"
    ML_MODEL_ROLLED_BACK = "ml.model_rolled_back"
    BACKTEST_RUN = "backtest.run"

    # Producción.
    LIVE_GATE_EVALUATED = "live.gate_evaluated"
    LIVE_APPROVAL_GRANTED = "live.approval_granted"
    LIVE_APPROVAL_REVOKED = "live.approval_revoked"
    LIVE_ENABLE_REJECTED = "live.enable_rejected"
    LIVE_ENABLED = "live.enabled"
    LIVE_DISABLED = "live.disabled"
    SAFE_MODE_ENTERED = "safe_mode.entered"
    SAFE_MODE_EXITED = "safe_mode.exited"
    KILL_SWITCH_ENGAGED = "kill_switch.engaged"
    KILL_SWITCH_RELEASED = "kill_switch.released"

    # Operación.
    BACKUP_CREATED = "backup.created"
    BACKUP_RESTORED = "backup.restored"
    BACKUP_ROTATED = "backup.rotated"
    UPDATE_CHECKED = "update.checked"
    UPDATE_APPLIED = "update.applied"
    UPDATE_ROLLED_BACK = "update.rolled_back"
    MAINTENANCE_STARTED = "maintenance.started"
    MAINTENANCE_FINISHED = "maintenance.finished"

    # Documentación, reportes y observación.
    NOTION_SYNCED = "notion.synced"
    REPORT_SENT = "report.sent"
    IMPROVEMENT_RECOMMENDED = "improvement.recommended"

    # Seguridad y alta disponibilidad.
    SECRET_ROTATION_DUE = "security.secret_rotation_due"
    RATE_LIMIT_EXCEEDED = "security.rate_limit_exceeded"
    FAILOVER_PROMOTED = "failover.promoted"
    FAILOVER_DEMOTED = "failover.demoted"
    LICENSE_VALIDATED = "license.validated"
