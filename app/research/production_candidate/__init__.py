"""Production Candidate + Promotion Manager (Fase 10).

Una candidata es una estrategia que superó el pipeline. El Promotion Manager es
*fail-closed*: sólo promueve si se cumplen todos los requisitos, no hay drift,
supera a la vigente y el operador aprueba. Toda decisión queda registrada. Nada
de esto habilita live trading por sí mismo.
"""

from app.research.production_candidate.candidate import CandidateStore, ProductionCandidate
from app.research.production_candidate.promotion import PromotionManager

__all__ = ["CandidateStore", "ProductionCandidate", "PromotionManager"]
