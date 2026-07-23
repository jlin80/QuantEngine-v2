"""Failover / alta disponibilidad (Fase 9): arriendo de líder primario/standby."""

from app.production.failover.coordinator import FailoverCoordinator

__all__ = ["FailoverCoordinator"]
