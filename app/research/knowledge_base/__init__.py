"""Knowledge Base (Fase 10): la memoria del laboratorio.

Registra qué funcionó, qué falló y por qué; en qué mercado, con qué parámetros y
en qué régimen. Append-only: el conocimiento nunca se pierde. Alimenta las
hipótesis futuras y evita repetir experimentos ya descartados.
"""

from app.research.knowledge_base.kb import KnowledgeBase

__all__ = ["KnowledgeBase"]
