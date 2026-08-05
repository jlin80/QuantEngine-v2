"""Estadística de correlación entre activos (Bloque 5) — funciones puras.

Todo lo de aquí trabaja sobre series de **rendimientos**, no de precios. Dos
precios que suben producen correlación alta aunque no tengan nada que ver: la
tendencia común domina el cálculo. Con rendimientos se mide lo que interesa,
que es si se mueven *juntos*, no si suben los dos.

Sobre la cointegración: aquí no hay un test ADF. Un ADF necesita tablas de
valores críticos y una implementación cuidadosa, y fingir un p-valor sería
peor que no darlo. Lo que se calcula es el **ratio de cobertura por mínimos
cuadrados y la vida media del residuo**: si el residuo revierte rápido, el par
se comporta como cointegrado; si no revierte, no. Es una medida honesta y
accionable, y se llama por su nombre en vez de disfrazarse de contraste
estadístico.
"""

import math
from collections.abc import Sequence
from itertools import pairwise

__all__ = [
    "correlation",
    "ewma_correlation",
    "hedge_ratio",
    "lead_lag",
    "residual_half_life",
    "returns",
]

_EPSILON = 1e-12
# Margen por debajo del cual dos correlaciones se consideran empatadas. Sin él,
# diferencias del orden del error de coma flotante deciden quién lidera.
_TIE_TOLERANCE = 1e-9


def returns(prices: Sequence[float]) -> list[float]:
    """Simple returns of a price series.

    Args:
        prices: Serie de precios en orden cronológico.

    Returns:
        Rendimientos (un elemento menos que la entrada). Los precios no
        positivos rompen el cociente, así que su paso se omite en vez de
        producir un infinito que contaminaría toda la correlación.
    """
    out: list[float] = []
    for previous, current in pairwise(prices):
        if previous <= 0:
            continue
        out.append(current / previous - 1.0)
    return out


def correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Pearson correlation of two aligned series.

    Args:
        left: Primera serie.
        right: Segunda serie.

    Returns:
        Correlación -1..1, o ``None`` con menos de dos puntos comunes o si
        alguna serie es constante (sin varianza no hay correlación definida;
        devolver 0.0 diría "no se mueven juntas", que no es lo mismo).
    """
    size = min(len(left), len(right))
    if size < 2:
        return None
    a = left[:size]
    b = right[:size]
    mean_a = sum(a) / size
    mean_b = sum(b) / size
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= _EPSILON or var_b <= _EPSILON:
        return None
    return cov / math.sqrt(var_a * var_b)


def ewma_correlation(
    left: Sequence[float], right: Sequence[float], halflife: float
) -> float | None:
    """Exponentially weighted correlation (correlación dinámica).

    Frente a la correlación rodante de ventana fija, esta no tiene un borde
    duro: una observación no pasa de pesar todo a pesar nada porque haya salido
    de la ventana. Eso importa aquí porque la correlación entre activos cambia
    justo en los momentos que interesa medir.

    Args:
        left: Primera serie.
        right: Segunda serie.
        halflife: Vida media del decaimiento, en observaciones.

    Returns:
        Correlación -1..1, o ``None`` si no es calculable.
    """
    size = min(len(left), len(right))
    if size < 2 or halflife <= 0:
        return None
    decay = 0.5 ** (1.0 / halflife)
    weights = [decay ** (size - 1 - i) for i in range(size)]
    total = sum(weights)
    if total <= _EPSILON:
        return None
    mean_a = sum(w * left[i] for i, w in enumerate(weights)) / total
    mean_b = sum(w * right[i] for i, w in enumerate(weights)) / total
    cov = float(sum(w * (left[i] - mean_a) * (right[i] - mean_b) for i, w in enumerate(weights)))
    var_a = float(sum(w * (left[i] - mean_a) ** 2 for i, w in enumerate(weights)))
    var_b = float(sum(w * (right[i] - mean_b) ** 2 for i, w in enumerate(weights)))
    if var_a <= _EPSILON or var_b <= _EPSILON:
        return None
    return cov / math.sqrt(var_a * var_b)


def lead_lag(
    left: Sequence[float], right: Sequence[float], max_lag: int
) -> tuple[int, float] | None:
    """Find the lag at which one series best explains the other.

    Convención de signo: **lag positivo = ``left`` va por delante**, es decir,
    el movimiento de ``left`` de hace ``lag`` pasos se parece al de ``right``
    de ahora. Es la convención que hace legible "quién lidera".

    Args:
        left: Serie candidata a líder.
        right: Serie candidata a seguidora.
        max_lag: Desplazamiento máximo a explorar, en observaciones.

    **Los empates los gana el desplazamiento más pequeño.** Una serie periódica
    correlaciona igual de bien consigo misma a lag 0 y a un múltiplo de su
    periodo; sin esta regla, el orden de iteración decidía cuál "ganaba" y el
    motor reportaba un liderazgo inventado entre dos series simultáneas. Ante la
    misma evidencia, la explicación más simple es que se mueven a la vez.

    Returns:
        ``(lag, correlación)`` del mejor desplazamiento por valor absoluto, o
        ``None`` si ninguno es calculable. El lag 0 entra en la comparación: si
        el mejor ajuste es simultáneo, no hay liderazgo que reportar.
    """
    best: tuple[int, float] | None = None
    # Se recorre por magnitud creciente para que, con la regla de empate de
    # abajo, el desplazamiento menor sea siempre el que se conserva.
    lags = sorted(range(-max_lag, max_lag + 1), key=lambda value: (abs(value), value))
    for lag in lags:
        if lag >= 0:
            a, b = left[: len(left) - lag] if lag else left, right[lag:]
        else:
            a, b = left[-lag:], right[: len(right) + lag]
        value = correlation(a, b)
        if value is None:
            continue
        if best is None or abs(value) > abs(best[1]) + _TIE_TOLERANCE:
            best = (lag, value)
    return best


def hedge_ratio(dependent: Sequence[float], independent: Sequence[float]) -> float | None:
    """Least-squares slope of ``dependent`` on ``independent``.

    Args:
        dependent: Serie dependiente (precios).
        independent: Serie independiente (precios).

    Returns:
        La pendiente, o ``None`` sin varianza en la independiente.
    """
    size = min(len(dependent), len(independent))
    if size < 2:
        return None
    mean_y = sum(dependent[:size]) / size
    mean_x = sum(independent[:size]) / size
    cov = sum((independent[i] - mean_x) * (dependent[i] - mean_y) for i in range(size))
    var = sum((independent[i] - mean_x) ** 2 for i in range(size))
    if var <= _EPSILON:
        return None
    return cov / var


def residual_half_life(residual: Sequence[float]) -> float | None:
    """Mean-reversion half-life of a residual series, in observations.

    Ajusta ``Δr = a + b·r`` y traduce la pendiente a vida media. Es el
    sustituto honesto de un contraste de cointegración: no dice "cointegrado con
    p<0.05", dice "el residuo vuelve a su media en N observaciones", que es la
    información con la que de verdad se opera un par.

    Args:
        residual: Serie del residuo.

    Returns:
        Vida media en observaciones, o ``None`` si el residuo **no** revierte
        (pendiente no negativa): en ese caso no hay vida media que dar, y
        devolver un número enorme sugeriría una reversión lentísima en vez de
        ninguna.
    """
    if len(residual) < 3:
        return None
    lagged = residual[:-1]
    deltas = [residual[i + 1] - residual[i] for i in range(len(residual) - 1)]
    slope = hedge_ratio(deltas, lagged)
    if slope is None or slope >= -_EPSILON:
        return None
    return -math.log(2.0) / math.log(1.0 + slope) if -1.0 < slope < 0.0 else 1.0
