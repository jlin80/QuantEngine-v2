"""Reportes operativos automáticos (Fase 9): resumen horario y reporte diario."""

from app.production.reporting.builder import OperationalReport, build_report
from app.production.reporting.service import ReportService

__all__ = ["OperationalReport", "ReportService", "build_report"]
