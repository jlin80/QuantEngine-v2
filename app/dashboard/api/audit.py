"""Audit log del dashboard — re-exporta el servicio de auditoría del motor.

La implementación vivía aquí desde la Fase 8. La Fase 9 la promovió a
``app/production/audit`` porque ya no audita sólo escrituras del dashboard:
también arranques, kill switch, safe mode, live gating, backups y
actualizaciones. Este módulo se conserva para no romper los imports existentes.
"""

from app.production.audit import AuditAction, AuditLog, audit_log

__all__ = ["AuditAction", "AuditLog", "audit_log"]
