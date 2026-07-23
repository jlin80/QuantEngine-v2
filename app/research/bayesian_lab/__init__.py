"""Bayesian Lab (Fase 10): optimización bayesiana con historial.

Implementa un Tree-structured Parzen Estimator (TPE) sin dependencias externas
—respeta el pin numpy<2/scipy y el VPS Bobcat—: modela las densidades de los
juegos "buenos" y "malos" y muestrea donde su razón es mayor. Guarda el historial
de cada corrida y compara resultados para evidenciar la mejora.
"""

from app.research.bayesian_lab.history import BayesianHistory
from app.research.bayesian_lab.lab import BayesianLab
from app.research.bayesian_lab.optimizer import BayesianResult, BayesianTrial, TPEOptimizer

__all__ = [
    "BayesianHistory",
    "BayesianLab",
    "BayesianResult",
    "BayesianTrial",
    "TPEOptimizer",
]
