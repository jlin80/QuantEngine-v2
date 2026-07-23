"""Registro de proveedores: añadir un exchange = registrar una factory.

El núcleo (feed/collector) no conoce ningún proveedor concreto; solo pide
instancias por nombre a este registro.
"""

from collections.abc import Callable

from app.config.settings import MarketSettings
from app.core.exceptions import ConfigurationError
from app.market.interfaces.provider import MarketDataProvider
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider
from app.market.providers.prepared import (
    BitgetProvider,
    IBKRProvider,
    MT5Provider,
    OandaProvider,
)

ProviderFactory = Callable[[MarketSettings], MarketDataProvider]
"""Fábrica que construye un proveedor a partir de la configuración."""


class ProviderRegistry:
    """Factory registry keyed by provider name.

    Args:
        settings: Sección ``market`` de la configuración.
    """

    def __init__(self, settings: MarketSettings) -> None:
        self._settings = settings
        self._factories: dict[str, ProviderFactory] = {}
        self._register_builtin()

    def _register_builtin(self) -> None:
        """Register the providers shipped with the engine."""
        self.register("binance", _binance)
        self.register("bybit", _bybit)
        self.register("okx", _okx)
        self.register("bitget", _bitget)
        self.register("oanda", _oanda)
        self.register("mt5", _mt5)
        self.register("ibkr", _ibkr)

    def register(self, name: str, factory: ProviderFactory) -> None:
        """Register (or replace) a provider factory.

        Args:
            name: Nombre único del proveedor.
            factory: Fábrica que lo construye.
        """
        self._factories[name] = factory

    @property
    def available(self) -> list[str]:
        """Names of every registered provider."""
        return sorted(self._factories)

    def create(self, name: str) -> MarketDataProvider:
        """Build a provider instance by name.

        Args:
            name: Nombre registrado.

        Returns:
            Instancia nueva del proveedor.

        Raises:
            ConfigurationError: Si el nombre no está registrado.
        """
        factory = self._factories.get(name)
        if factory is None:
            raise ConfigurationError(
                f"Unknown market provider '{name}'",
                context={"available": self.available},
            )
        return factory(self._settings)


def _binance(settings: MarketSettings) -> MarketDataProvider:
    """Factory de Binance."""
    return BinanceProvider(settings.provider_settings("binance"), settings.ws)


def _bybit(settings: MarketSettings) -> MarketDataProvider:
    """Factory de Bybit."""
    return BybitProvider(settings.provider_settings("bybit"), settings.ws)


def _okx(settings: MarketSettings) -> MarketDataProvider:
    """Factory de OKX."""
    return OKXProvider(settings.provider_settings("okx"), settings.ws)


def _bitget(settings: MarketSettings) -> MarketDataProvider:
    """Factory de Bitget (preparado)."""
    return BitgetProvider(settings.provider_settings("bitget"))


def _oanda(settings: MarketSettings) -> MarketDataProvider:
    """Factory de OANDA (preparado)."""
    return OandaProvider(settings.provider_settings("oanda"))


def _mt5(settings: MarketSettings) -> MarketDataProvider:
    """Factory de MetaTrader 5 (preparado)."""
    return MT5Provider(settings.provider_settings("mt5"))


def _ibkr(settings: MarketSettings) -> MarketDataProvider:
    """Factory de Interactive Brokers (preparado)."""
    return IBKRProvider(settings.provider_settings("ibkr"))


__all__ = ["ProviderFactory", "ProviderRegistry"]
