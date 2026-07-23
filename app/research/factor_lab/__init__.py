"""Factor Lab (Fase 10): investigación de factores estilo fondo cuantitativo.

Investiga automáticamente factores de tendencia, reversión, liquidez, volatilidad,
temporales, de volumen e híbridos, y los ordena por su coeficiente de información
contra el retorno futuro. Ranking automático, reproducible y sin lookahead.
"""

from app.research.factor_lab.lab import FactorLab

__all__ = ["FactorLab"]
