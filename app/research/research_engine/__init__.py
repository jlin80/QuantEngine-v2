"""Research Engine (Fase 10): orquestación autónoma del laboratorio.

Envuelve al :class:`~app.research.api.ResearchLab` y expone los ciclos de
investigación de alto nivel que dispararían el scheduler o el dashboard
(generar→validar→rankear, investigar factores, correr Shadow Mode). No añade
lógica de negocio: coordina los motores del laboratorio.
"""

from app.research.research_engine.engine import ResearchEngine

__all__ = ["ResearchEngine"]
