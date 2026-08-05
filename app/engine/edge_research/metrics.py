"""Métricas de salud del edge — funciones puras sobre series de R.

Todas reciben la serie de resultados **en orden cronológico** (el más antiguo
primero) y devuelven ``None`` cuando la muestra no da para responder, en vez de
un cero que se confundiría con "medido y sale cero". Esa distinción es el motivo
de que casi todo el módulo devuelva opcionales: un motor que no sabe si tiene
edge no puede reportar lo mismo que uno que sabe que no lo tiene.

Ninguna función mira el reloj ni toca disco: son puras a propósito, para que el
Edge Research Engine sea testeable sin construir mercado, y para que el
backtesting pueda reusarlas sobre histórico sin arrastrar servicios.
"""

import math
from collections.abc import Sequence

__all__ = [
    "block_expectancies",
    "confidence_drift",
    "edge_decay",
    "edge_persistence",
    "expectancy",
    "half_life",
    "max_drawdown",
    "ols_slope",
    "profit_factor",
    "sharpe",
    "sortino",
    "stability_score",
]

# Suelo numérico común: separa "denominador nulo" de "denominador diminuto".
_EPSILON = 1e-9


def expectancy(values: Sequence[float]) -> float | None:
    """Resultado medio por operación, en R.

    Args:
        values: Serie de R por operación resuelta.

    Returns:
        La media, o ``None`` sin muestra.
    """
    if not values:
        return None
    return sum(values) / len(values)


def profit_factor(values: Sequence[float]) -> float | None:
    """Ganancia bruta / pérdida bruta, en R.

    Args:
        values: Serie de R por operación resuelta.

    Returns:
        El profit factor, o ``None`` si no hay pérdidas (sin pérdidas el
        cociente es infinito, que como métrica no informa de nada).
    """
    gross_loss = sum(-v for v in values if v < 0)
    if gross_loss <= _EPSILON:
        return None
    gross_win = sum(v for v in values if v > 0)
    return gross_win / gross_loss


def sharpe(values: Sequence[float]) -> float | None:
    """Sharpe **por operación**, sin anualizar.

    Deliberadamente no se anualiza: anualizar exige una frecuencia de operación
    estable, y en scalping la frecuencia depende del régimen. Un número por
    operación es comparable entre estrategias sin inventarse esa estabilidad.

    Args:
        values: Serie de R por operación resuelta.

    Returns:
        Media / desviación típica muestral, o ``None`` con menos de dos
        operaciones o con dispersión nula.
    """
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    deviation = math.sqrt(variance)
    if deviation <= _EPSILON:
        return None
    return mean / deviation


def sortino(values: Sequence[float]) -> float | None:
    """Sortino por operación (objetivo 0 R), sin anualizar.

    Args:
        values: Serie de R por operación resuelta.

    Returns:
        Media / desviación de los resultados negativos, o ``None`` con menos de
        dos operaciones o sin resultados negativos.
    """
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    downside = [v for v in values if v < 0.0]
    if not downside:
        return None
    deviation = math.sqrt(sum(v**2 for v in downside) / len(downside))
    if deviation <= _EPSILON:
        return None
    return mean / deviation


def max_drawdown(values: Sequence[float]) -> float:
    """Máximo drawdown de la curva acumulada de R.

    Args:
        values: Serie de R por operación resuelta.

    Returns:
        El drawdown máximo en R (0.0 sin muestra, o si nunca cayó del pico).
    """
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def ols_slope(values: Sequence[float]) -> float | None:
    """Pendiente de la recta de mínimos cuadrados contra el índice.

    Args:
        values: Serie ordenada cronológicamente.

    Returns:
        Cambio por paso de índice, o ``None`` con menos de dos puntos.
    """
    n = len(values)
    if n < 2:
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    covariance = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    variance = sum((i - mean_x) ** 2 for i in range(n))
    if variance <= _EPSILON:
        return None
    return covariance / variance


def block_expectancies(values: Sequence[float], blocks: int) -> list[float]:
    """Expectativa media de cada bloque contiguo de la serie.

    Trocear en bloques —en vez de mirar operación a operación— es lo que hace
    legibles decay, estabilidad y persistencia: una sola operación mala no es
    deterioro del edge, y una racha de un bloque entero sí es señal.

    Args:
        values: Serie de R por operación resuelta.
        blocks: Nº de bloques deseado (se recorta si no hay muestra para tantos).

    Returns:
        Una expectativa por bloque, en orden cronológico. Lista vacía si no hay
        muestra suficiente para al menos un bloque completo.
    """
    if blocks < 1 or not values:
        return []
    usable = min(blocks, len(values))
    size = len(values) // usable
    if size < 1:
        return []
    # El resto se descarta por la cola ANTIGUA: los bloques deben terminar en la
    # operación más reciente, porque decay y persistencia se leen hacia el final.
    trimmed = values[len(values) - size * usable :]
    result: list[float] = []
    for index in range(usable):
        chunk = trimmed[index * size : (index + 1) * size]
        result.append(sum(chunk) / len(chunk))
    return result


def edge_decay(values: Sequence[float], blocks: int) -> float | None:
    """Ritmo al que se deteriora la expectativa, en R por bloque.

    Convención de signo: **positivo = el edge se está encogiendo**. Es el
    negativo de la pendiente, porque la métrica se llama "decay" y un número
    grande tiene que significar mala noticia sin necesidad de leer la doc.

    Args:
        values: Serie de R por operación resuelta.
        blocks: Nº de bloques en que se trocea la serie.

    Returns:
        R perdida por bloque, o ``None`` sin bloques suficientes.
    """
    expectancies = block_expectancies(values, blocks)
    slope = ols_slope(expectancies)
    return None if slope is None else -slope


def half_life(values: Sequence[float], blocks: int) -> float | None:
    """Operaciones que le quedan al edge para valer la mitad, si sigue así.

    Extrapolación **lineal** del decay medido, no un modelo exponencial: con
    decenas o cientos de operaciones por estrategia, ajustar una exponencial es
    darle una precisión que la muestra no tiene. Es una alarma de orden de
    magnitud, no una predicción.

    Args:
        values: Serie de R por operación resuelta.
        blocks: Nº de bloques en que se trocea la serie.

    Returns:
        Operaciones restantes estimadas, o ``None`` si no aplica — sin decay
        (el edge no se encoge) o sin expectativa positiva que perder.
    """
    expectancies = block_expectancies(values, blocks)
    if not expectancies:
        return None
    decay = edge_decay(values, blocks)
    if decay is None or decay <= _EPSILON:
        return None
    current = expectancies[-1]
    if current <= _EPSILON:
        return None
    block_size = len(values) // len(expectancies)
    return (current / 2.0) / decay * block_size


def stability_score(values: Sequence[float], blocks: int) -> float | None:
    """Consistencia del edge entre bloques, en 0-1 (1 = idéntico en todos).

    Se define como ``1 / (1 + cv)`` sobre las expectativas por bloque. Frente a
    ``1 - cv``, esta forma no se satura en 0 en cuanto el coeficiente de
    variación pasa de 1, que es justo el rango donde viven las estrategias de
    scalping: así sigue distinguiendo "irregular" de "puro ruido".

    Args:
        values: Serie de R por operación resuelta.
        blocks: Nº de bloques en que se trocea la serie.

    Returns:
        Puntuación 0-1, o ``None`` con menos de dos bloques.
    """
    expectancies = block_expectancies(values, blocks)
    if len(expectancies) < 2:
        return None
    mean = sum(expectancies) / len(expectancies)
    variance = sum((v - mean) ** 2 for v in expectancies) / (len(expectancies) - 1)
    deviation = math.sqrt(variance)
    coefficient = deviation / (abs(mean) + _EPSILON)
    return 1.0 / (1.0 + coefficient)


def edge_persistence(values: Sequence[float], blocks: int) -> float | None:
    """Fracción de bloques con expectativa positiva.

    Responde a algo que la media esconde: un edge que sale de dos bloques
    excelentes y cuatro perdedores no es un edge, es un evento.

    Args:
        values: Serie de R por operación resuelta.
        blocks: Nº de bloques en que se trocea la serie.

    Returns:
        Fracción 0-1, o ``None`` sin bloques.
    """
    expectancies = block_expectancies(values, blocks)
    if not expectancies:
        return None
    positive = sum(1 for value in expectancies if value > 0.0)
    return positive / len(expectancies)


def confidence_drift(values: Sequence[float], blocks: int) -> float | None:
    """Deriva de la confianza declarada por las señales, por bloque.

    Positivo = la estrategia se está declarando **más** confiada con el tiempo.
    Cruzado con el decay, es la señal de sobreconfianza: confianza subiendo
    mientras la expectativa baja.

    Args:
        values: Serie de confianzas (0-1) en orden cronológico.
        blocks: Nº de bloques en que se trocea la serie.

    Returns:
        Cambio de confianza por bloque, o ``None`` sin bloques suficientes.
    """
    return ols_slope(block_expectancies(values, blocks))
