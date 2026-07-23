"""Quant Research Lab (Fase 10): laboratorio cuantitativo autoevolutivo.

Módulo **independiente de producción**. Su objetivo no es operar: descubre,
prueba y valida nuevas estrategias automáticamente, y sólo promueve las mejores
—siempre con aprobación humana—. Nunca modifica estrategias en producción
(trabaja sobre copias) ni habilita live trading mientras experimenta.

La fachada pública es :class:`~app.research.api.ResearchLab`.
"""

from app.research.api import ResearchLab
from app.research.research_engine import ResearchEngine

__all__ = ["ResearchEngine", "ResearchLab"]
