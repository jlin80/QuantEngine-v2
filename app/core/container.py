"""Contenedor de inyección de dependencias.

Minimalista y explícito (KISS): registro por tipo, instancias o factories,
singletons por defecto. Evita acoplar módulos entre sí — los módulos reciben
sus dependencias por constructor y el contenedor solo se usa en el bootstrap.
"""

from collections.abc import Callable
from typing import Any, TypeVar, cast

from app.core.exceptions import DependencyResolutionError

T = TypeVar("T")


class Container:
    """Typed service registry used at composition root only."""

    def __init__(self) -> None:
        self._instances: dict[type[Any], Any] = {}
        self._factories: dict[type[Any], Callable[[], Any]] = {}

    def register_instance(self, interface: type[T], instance: T) -> None:
        """Register an already-built singleton instance.

        Args:
            interface: Type (or protocol) under which the instance is exposed.
            instance: The object to serve on :meth:`resolve`.
        """
        self._instances[interface] = instance

    def register_factory(self, interface: type[T], factory: Callable[[], T]) -> None:
        """Register a lazy factory; the result is cached as a singleton.

        Args:
            interface: Type under which the dependency is exposed.
            factory: Zero-argument callable building the instance.
        """
        self._factories[interface] = factory

    def resolve(self, interface: type[T]) -> T:
        """Resolve a dependency by type.

        Args:
            interface: The registered type to look up.

        Returns:
            The singleton instance for ``interface``.

        Raises:
            DependencyResolutionError: If nothing is registered for the type.
        """
        if interface in self._instances:
            return cast(T, self._instances[interface])
        if interface in self._factories:
            instance = self._factories[interface]()
            self._instances[interface] = instance
            return cast(T, instance)
        raise DependencyResolutionError(
            f"No dependency registered for type '{interface.__name__}'",
            context={"requested": interface.__name__},
        )

    def contains(self, interface: type[Any]) -> bool:
        """Return whether a dependency is registered for ``interface``."""
        return interface in self._instances or interface in self._factories
