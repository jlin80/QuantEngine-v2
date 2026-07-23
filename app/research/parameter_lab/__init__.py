"""Parameter Lab (Fase 10): describe el espacio de parámetros de un genoma.

Traduce los dominios declarados en el catálogo de bloques/filtros a un
:class:`~app.backtesting.optimizer.space.ParameterSpace` con claves estables
(``b{i}_{param}`` / ``f{i}_{param}``), y ofrece la *source factory* que los
optimizadores y el walk-forward usan para reconstruir la estrategia desde un
vector de parámetros —siempre sobre una copia del genoma.
"""

from app.research.parameter_lab.spaces import (
    build_source_factory,
    build_space,
    space_size,
)

__all__ = ["build_source_factory", "build_space", "space_size"]
