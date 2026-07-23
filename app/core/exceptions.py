"""Jerarquía de excepciones del sistema.

Reglas del proyecto:
    * Nunca usar ``except:`` genérico — capturar tipos concretos.
    * Todo error lleva contexto (``context``) y clasificación (``recoverable``).
    * Los módulos capturan sus propias excepciones y deciden si escalan.
"""

from typing import Any


class QuantEngineError(Exception):
    """Base exception for every error raised by the engine.

    Attributes:
        message: Human-readable description of the error.
        context: Structured data useful for logging and diagnosis.
        recoverable: Whether the system may continue operating after the error.
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        recoverable: bool = True,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context or {}
        self.recoverable = recoverable

    def __str__(self) -> str:
        """Return the message enriched with context when present."""
        if self.context:
            return f"{self.message} | context={self.context}"
        return self.message


class ConfigurationError(QuantEngineError):
    """Invalid, missing or inconsistent configuration. Not recoverable."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, context=context, recoverable=False)


class EventBusError(QuantEngineError):
    """Failure inside the event bus (publish on stopped bus, queue overflow...)."""


class ServiceLifecycleError(QuantEngineError):
    """A service failed to start, stop or transitioned illegally."""


class DependencyResolutionError(QuantEngineError):
    """The DI container cannot resolve a requested dependency."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, context=context, recoverable=False)


class NotificationError(QuantEngineError):
    """A notification channel failed to deliver a message."""


class CacheError(QuantEngineError):
    """Cache backend failure. Always recoverable: the system degrades to memory."""


class DatabaseError(QuantEngineError):
    """Database connectivity or query failure."""


class SchedulerError(QuantEngineError):
    """Scheduler failure (duplicate job, invalid interval...)."""


class WatchdogError(QuantEngineError):
    """Watchdog failure (unknown component, restart exhausted...)."""


class HealthCheckError(QuantEngineError):
    """A health probe could not be collected."""


class ValidationError(QuantEngineError):
    """Input data failed validation at a module boundary."""


class MarketDataError(QuantEngineError):
    """Base for every Data Engine failure."""


class ProviderError(MarketDataError):
    """A market-data provider failed (conexión, REST, suscripción...)."""


class NormalizationError(MarketDataError):
    """A raw exchange message could not be translated to the internal model."""


class OrderBookDesyncError(MarketDataError):
    """The incremental order book lost sequence and needs a REST rebuild."""


class MLError(QuantEngineError):
    """Base for every Machine Learning layer failure (Fase 7)."""


class ModelNotFittedError(MLError):
    """A prediction was requested from a model that has not been trained."""


class ModelBackendUnavailableError(MLError):
    """An optional model backend (XGBoost/LightGBM/CatBoost/NN) is not installed.

    La infraestructura queda preparada para estos backends, pero no se instalan
    por defecto (dependencias pesadas). Al invocarlos sin la librería presente se
    lanza este error con un mensaje claro, igual que en el laboratorio (Fase 6).
    """

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, context=context, recoverable=True)


class ModelValidationError(MLError):
    """A trained model failed the validation gate and cannot be activated."""


class InsufficientDataError(MLError):
    """Not enough samples to train, validate or analyse."""


class ModelRegistryError(MLError):
    """The model registry could not satisfy an operation (unknown id, overwrite...)."""


class ResearchError(QuantEngineError):
    """Base for every Quant Research Lab failure (Fase 10).

    El laboratorio es independiente de producción: sus fallos nunca detienen el
    motor ni tocan live trading. Se degradan con elegancia y quedan registrados.
    """


class StrategyGenerationError(ResearchError):
    """An experimental strategy could not be generated from its genome."""


class ExperimentNotFoundError(ResearchError):
    """The requested experiment does not exist in the store."""


class PromotionBlockedError(ResearchError):
    """A candidate cannot be promoted: a hard requirement is not met.

    El Promotion Manager es *fail-closed*: ante la duda, no promueve. Este error
    lleva en el contexto los motivos exactos del bloqueo para la auditoría.
    """
