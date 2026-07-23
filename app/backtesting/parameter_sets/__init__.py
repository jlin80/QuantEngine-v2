"""Catálogo de conjuntos de parámetros (Fase 6).

Los conjuntos de parámetros versionados viven en :mod:`app.backtesting.versioning`;
este módulo los reexporta como la vista de "catálogo" que pide la especificación.
"""

from app.backtesting.versioning import ParameterSet, VersionStore

__all__ = ["ParameterSet", "VersionStore"]
