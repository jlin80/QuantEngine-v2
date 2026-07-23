"""Report Generator (Fase 10): informes del laboratorio.

Construye informes JSON/Markdown de experimentos, ranking, candidatas y Shadow
Mode, y los persiste bajo demanda. Alimenta el dashboard, Discord y Notion.
"""

from app.research.report_generator.reporter import ResearchReporter

__all__ = ["ResearchReporter"]
