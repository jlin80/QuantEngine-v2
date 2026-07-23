"""Simulation Cluster (Fase 10): evalúa muchos backtests en paralelo.

Reparte trabajos independientes (backtests de genomas) sobre un pool de hilos
acotado, manteniendo el event-loop libre. La abstracción permite escalar a
procesos en el futuro sin tocar a los llamadores. Determinista en el orden de
salida (respeta el orden de entrada).
"""

from app.research.simulation_cluster.cluster import SimulationCluster

__all__ = ["SimulationCluster"]
