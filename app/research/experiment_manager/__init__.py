"""Experiment Manager (Fase 10): registro append-only de experimentos.

Crea, guarda, recupera y archiva experimentos con su hipótesis, resultados y
conclusiones. Nunca reescribe el histórico: cada cambio se anexa y la última
versión gana en memoria. El conocimiento no se pierde.
"""

from app.research.experiment_manager.manager import ExperimentManager

__all__ = ["ExperimentManager"]
