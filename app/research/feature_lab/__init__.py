"""Feature Lab (Fase 10): experimenta y valida nuevas features.

Cada feature candidata (ATR Slope, VWAP Distance, Delta Momentum, Liquidity
Score, Trend Score, Book Pressure, Microprice, Spread Velocity, Volatility
Expansion...) se valida antes de poder usarse: cobertura, varianza y coeficiente
de información contra el retorno futuro. Una feature que no aporta señal no entra.
"""

from app.research.feature_lab.lab import FeatureLab

__all__ = ["FeatureLab"]
