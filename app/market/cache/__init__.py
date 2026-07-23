"""Cache de mercado (Redis con degradación a memoria)."""

from app.market.cache.market_cache import MarketCache

__all__ = ["MarketCache"]
