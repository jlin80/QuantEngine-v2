"""Espacio de parámetros para la optimización (Fase 6).

Un :class:`ParameterSpace` describe qué parámetros optimizar y con qué valores
candidatos: conjuntos discretos (para grid search) o rangos continuos/enteros
(para random, genético y bayesiano). Todo parámetro del sistema —ATR, VWAP,
EMA, delta, spread máximo, score/confianza mínimos, stop, take profit, R:R,
horario...— puede describirse aquí sin tocar el código de ninguna estrategia.
"""

import itertools
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """Definición de un parámetro optimizable.

    Attributes:
        name: Nombre del parámetro.
        choices: Valores discretos (si es un conjunto enumerado).
        low: Cota inferior (si es un rango).
        high: Cota superior (si es un rango).
        is_int: Si el rango debe muestrearse como entero.
    """

    name: str
    choices: tuple[Any, ...] | None = None
    low: float | None = None
    high: float | None = None
    is_int: bool = False

    @property
    def is_discrete(self) -> bool:
        """Whether the spec enumerates discrete choices."""
        return self.choices is not None

    def sample(self, rng: random.Random) -> Any:
        """Draw one random value from the spec.

        Args:
            rng: Fuente aleatoria.

        Returns:
            Un valor válido del parámetro.

        Raises:
            ValueError: Si el spec no tiene ni choices ni rango.
        """
        if self.choices is not None:
            return rng.choice(self.choices)
        if self.low is None or self.high is None:
            raise ValueError(f"Parámetro sin dominio: {self.name}")
        if self.is_int:
            return rng.randint(int(self.low), int(self.high))
        return rng.uniform(self.low, self.high)


class ParameterSpace:
    """Collection of parameter specs to optimize over."""

    def __init__(self) -> None:
        self._specs: dict[str, ParameterSpec] = {}

    def add_choices(self, name: str, values: Sequence[Any]) -> "ParameterSpace":
        """Add a discrete parameter.

        Args:
            name: Nombre del parámetro.
            values: Valores candidatos.

        Returns:
            El propio espacio (encadenable).

        Raises:
            ValueError: Si ``values`` está vacío.
        """
        if not values:
            raise ValueError(f"El parámetro {name} necesita al menos un valor")
        self._specs[name] = ParameterSpec(name=name, choices=tuple(values))
        return self

    def add_range(
        self, name: str, low: float, high: float, *, is_int: bool = False
    ) -> "ParameterSpace":
        """Add a continuous (or integer) range parameter.

        Args:
            name: Nombre del parámetro.
            low: Cota inferior.
            high: Cota superior.
            is_int: Si debe muestrearse como entero.

        Returns:
            El propio espacio (encadenable).

        Raises:
            ValueError: Si ``low > high``.
        """
        if low > high:
            raise ValueError(f"Rango inválido para {name}: {low} > {high}")
        self._specs[name] = ParameterSpec(name=name, low=low, high=high, is_int=is_int)
        return self

    @property
    def names(self) -> list[str]:
        """Names of the parameters in the space."""
        return list(self._specs)

    @property
    def specs(self) -> list[ParameterSpec]:
        """The parameter specs."""
        return list(self._specs.values())

    def grid(self) -> Iterator[dict[str, Any]]:
        """Iterate the full cartesian product of discrete parameters.

        Yields:
            Un diccionario de parámetros por combinación.

        Raises:
            ValueError: Si algún parámetro es un rango continuo (no enumerable).
        """
        names: list[str] = []
        value_lists: list[tuple[Any, ...]] = []
        for spec in self._specs.values():
            if spec.choices is None:
                raise ValueError(f"grid() requiere parámetros discretos; {spec.name} es un rango")
            names.append(spec.name)
            value_lists.append(spec.choices)
        if not names:
            return
        for combo in itertools.product(*value_lists):
            yield dict(zip(names, combo, strict=True))

    def grid_size(self) -> int:
        """Number of combinations the grid would produce."""
        size = 1
        for spec in self._specs.values():
            if spec.choices is None:
                return 0
            size *= len(spec.choices)
        return size if self._specs else 0

    def sample(self, rng: random.Random) -> dict[str, Any]:
        """Draw a random parameter set from the space.

        Args:
            rng: Fuente aleatoria.

        Returns:
            Un diccionario de parámetros muestreado.
        """
        return {name: spec.sample(rng) for name, spec in self._specs.items()}
