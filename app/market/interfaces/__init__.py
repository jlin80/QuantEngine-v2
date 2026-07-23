"""Interfaces del Data Engine (los módulos dependen de esto, no de impls)."""

from app.market.interfaces.normalizer import Normalizer
from app.market.interfaces.provider import MarketDataProvider, Sink, StateCallback
from app.market.interfaces.storage import MarketStore

__all__ = ["MarketDataProvider", "MarketStore", "Normalizer", "Sink", "StateCallback"]
