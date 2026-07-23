"""Contrato de los normalizadores por exchange."""

import abc
from typing import Any

from app.market.models import MarketObject


class Normalizer(abc.ABC):
    """Convierte mensajes crudos de UN exchange al modelo interno.

    Cada exchange tiene su normalizador; todos producen exactamente los
    mismos objetos (:data:`~app.market.models.MarketObject`) con timestamps
    UTC (exchange + local) y símbolo interno.
    """

    @abc.abstractmethod
    def normalize(self, message: dict[str, Any]) -> list[MarketObject]:
        """Translate one raw message into internal objects.

        Args:
            message: Mensaje ya deserializado (dict) del exchange.

        Returns:
            Cero o más objetos internos (los mensajes de control devuelven
            lista vacía).

        Raises:
            NormalizationError: Si el mensaje es irreconocible o corrupto.
        """
