"""Modelo de slippage por volumen, liquidez, volatilidad, horario y orden."""

from app.execution.slippage.engine import SlippageContext, SlippageEngine

__all__ = ["SlippageContext", "SlippageEngine"]
