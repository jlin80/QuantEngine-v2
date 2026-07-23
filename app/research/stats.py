"""Utilidades estadísticas puras del laboratorio (Fase 10).

Sin dependencias externas (respeta el pin numpy<2 / scipy del proyecto y el VPS
Bobcat sin SSE4.2): medias, varianzas, correlación de Pearson, coeficiente de
información (IC) contra el retorno futuro y una prueba t de Welch para comparar
dos muestras (usada por el Shadow Mode). Todo determinista y JSON-safe.
"""

import math
from collections.abc import Sequence


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean (0.0 for an empty sequence)."""
    return sum(values) / len(values) if values else 0.0


def variance(values: Sequence[float]) -> float:
    """Sample variance (0.0 when fewer than two values)."""
    n = len(values)
    if n < 2:
        return 0.0
    mu = mean(values)
    return sum((v - mu) ** 2 for v in values) / (n - 1)


def stdev(values: Sequence[float]) -> float:
    """Sample standard deviation."""
    return math.sqrt(variance(values))


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson correlation coefficient of two equal-length series.

    Returns 0.0 when the series are too short or either has zero variance
    (a constant series carries no linear information).
    """
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs = xs[:n]
    ys = ys[:n]
    mx = mean(xs)
    my = mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0.0 or vy <= 0.0:
        return 0.0
    return cov / math.sqrt(vx * vy)


def forward_returns(prices: Sequence[float], horizon: int) -> list[float]:
    """Return the forward log-ish return over ``horizon`` bars for each index.

    The output aligns with ``prices[:-horizon]``: element ``i`` is the fractional
    change from ``prices[i]`` to ``prices[i + horizon]``.
    """
    if horizon < 1:
        raise ValueError("horizon debe ser >= 1")
    out: list[float] = []
    for i in range(len(prices) - horizon):
        base = prices[i]
        if base == 0.0:
            out.append(0.0)
            continue
        out.append((prices[i + horizon] - base) / base)
    return out


def information_coefficient(
    feature: Sequence[float], prices: Sequence[float], horizon: int
) -> tuple[float, int]:
    """Correlation between a feature series and the forward return.

    Aligns the feature with the forward return over ``horizon`` bars, dropping
    non-finite feature values.

    Returns:
        ``(ic, samples)`` — the Pearson IC and the number of aligned points.
    """
    fwd = forward_returns(prices, horizon)
    n = min(len(feature), len(fwd))
    xs: list[float] = []
    ys: list[float] = []
    for i in range(n):
        value = feature[i]
        if math.isfinite(value):
            xs.append(value)
            ys.append(fwd[i])
    if len(xs) < 3:
        return 0.0, len(xs)
    return pearson(xs, ys), len(xs)


def hit_rate(feature: Sequence[float], prices: Sequence[float], horizon: int) -> float:
    """Fraction of bars where the feature sign matches the forward-return sign."""
    fwd = forward_returns(prices, horizon)
    n = min(len(feature), len(fwd))
    hits = 0
    total = 0
    for i in range(n):
        value = feature[i]
        if not math.isfinite(value) or value == 0.0 or fwd[i] == 0.0:
            continue
        total += 1
        if (value > 0) == (fwd[i] > 0):
            hits += 1
    return hits / total if total else 0.0


def equity_stability(equity: Sequence[float]) -> float:
    """Smoothness of an equity curve in ``[0, 1]`` (R² vs a straight line).

    A perfectly linear equity growth scores 1.0; a jagged, erratic curve scores
    near 0.0. Un complemento de "estabilidad" para el objetivo multiobjetivo y
    el ranking, computado sin dependencias externas.
    """
    n = len(equity)
    if n < 3:
        return 0.0
    xs = [float(i) for i in range(n)]
    mx = mean(xs)
    my = mean(equity)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, equity, strict=True))
    if sxx <= 0.0:
        return 0.0
    slope_hat = sxy / sxx
    intercept = my - slope_hat * mx
    ss_res = sum((y - (slope_hat * x + intercept)) ** 2 for x, y in zip(xs, equity, strict=True))
    ss_tot = sum((y - my) ** 2 for y in equity)
    if ss_tot <= 0.0:
        return 1.0 if ss_res <= 0.0 else 0.0
    return max(0.0, min(1.0, 1.0 - ss_res / ss_tot))


def welch_t_test(sample_a: Sequence[float], sample_b: Sequence[float]) -> tuple[float, float]:
    """Welch's t-test for the difference of means (unequal variances).

    Returns:
        ``(t_stat, p_value)`` with a two-sided p-value from a normal
        approximation of the t distribution (adequate for n >= ~30, which the
        Shadow Mode requires before concluding).
    """
    na, nb = len(sample_a), len(sample_b)
    if na < 2 or nb < 2:
        return 0.0, 1.0
    ma, mb = mean(sample_a), mean(sample_b)
    va, vb = variance(sample_a), variance(sample_b)
    se_sq = va / na + vb / nb
    if se_sq <= 0.0:
        return 0.0, 1.0
    t_stat = (ma - mb) / math.sqrt(se_sq)
    p_value = 2.0 * (1.0 - _normal_cdf(abs(t_stat)))
    return t_stat, max(0.0, min(1.0, p_value))


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
