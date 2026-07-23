"""Auditoría permanente del sistema (append-only, nunca se borra)."""

from app.production.audit.service import AuditLog, audit_log
from app.production.audit.taxonomy import AuditAction

__all__ = ["AuditAction", "AuditLog", "audit_log"]
