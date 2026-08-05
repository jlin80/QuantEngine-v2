"""Regime Forecast Engine (Bloque 4) — pronosticar, no sólo detectar.

El método es deliberadamente simple: **frecuencia condicional empírica**. Se
observa la condición actual (régimen + banda de volatilidad), se mira qué pasó
históricamente las veces que el mercado estuvo en esa misma condición, y ese
reparto es el pronóstico. Nada de modelos ocultos ni cadenas de Markov
ajustadas: con la muestra que tiene este proyecto, un modelo más rico produciría
parámetros peor estimados y un número más difícil de auditar.

**Lo que hace honesto al bloque es la validación, no el pronóstico.** Todo
pronóstico se guarda, se resuelve contra lo que realmente pasó y se puntúa con
Brier — contra el pronóstico trivial. Un Brier de 0.6 no dice nada hasta saber
que el trivial saca 0.7. Si el `skill` es negativo, el motor lo publica igual:
es la señal de que este pronóstico no vale para decidir nada.
"""

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config.settings import QuantRegimeForecastSettings
from app.engine.models import Regime, VolatilityState
from app.engine.regime_forecast.models import OUTCOMES, ForecastScore, RegimeForecast
from app.market.models import Candle
from app.utils.time import utc_now


@dataclass(kw_only=True, slots=True)
class _Pending:
    """Un pronóstico emitido y todavía sin desenlace conocido."""

    symbol: str
    at: datetime
    condition: str
    probabilities: dict[str, float]
    anchor_index: int


class RegimeForecastEngine:
    """Forecast the next regime outcome from conditional empirical frequency.

    Args:
        settings: Horizonte, mínimos y suavizado.
    """

    def __init__(self, settings: QuantRegimeForecastSettings) -> None:
        self._settings = settings
        self._counts: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(OUTCOMES, 0))
        self._global: dict[str, int] = dict.fromkeys(OUTCOMES, 0)
        self._pending: dict[str, deque[_Pending]] = {}
        self._resolved: deque[tuple[dict[str, float], str]] = deque(
            maxlen=settings.validation_window
        )
        self._log = logging.getLogger("app.engine.regime_forecast")

    # ------------------------------------------------------------------
    # Aprendizaje
    # ------------------------------------------------------------------

    def learn(self, condition: str, outcome: str) -> None:
        """Record one resolved (condition → outcome) observation.

        Args:
            condition: Estado desde el que se observó.
            outcome: Desenlace realizado (uno de :data:`OUTCOMES`).
        """
        if outcome not in OUTCOMES:
            return
        self._counts[condition][outcome] += 1
        self._global[outcome] += 1

    def learn_from_candles(
        self, candles: list[Candle], regime: Regime, volatility: VolatilityState
    ) -> int:
        """Learn every (condition → outcome) pair contained in a candle series.

        La condición se toma como constante en toda la serie a propósito: el
        detector clasifica el estado *actual*, no el de cada vela pasada, y
        reconstruirlo hacia atrás exigiría re-detectar con datos que en su
        momento no existían — lookahead disfrazado de backfill.

        Args:
            candles: Serie cronológica.
            regime: Régimen atribuido a la serie.
            volatility: Banda de volatilidad atribuida a la serie.

        Returns:
            Cuántas observaciones se aprendieron.
        """
        condition = self.condition_of(regime, volatility)
        horizon = self._settings.horizon_bars
        learned = 0
        for index in range(len(candles) - horizon):
            outcome = classify_outcome(candles, index, horizon)
            if outcome is not None:
                self.learn(condition, outcome)
                learned += 1
        return learned

    @staticmethod
    def condition_of(regime: Regime, volatility: VolatilityState) -> str:
        """Build the conditioning key from regime and volatility band."""
        return f"{regime.value}|{volatility.value}"

    # ------------------------------------------------------------------
    # Pronóstico
    # ------------------------------------------------------------------

    def forecast(self, symbol: str, regime: Regime, volatility: VolatilityState) -> RegimeForecast:
        """Probability of each outcome for the configured horizon.

        Args:
            symbol: Activo.
            regime: Régimen actual.
            volatility: Banda de volatilidad actual.

        Returns:
            El pronóstico. Sin muestra suficiente para la condición devuelve
            ``observable=False`` con su motivo, en vez de repartir a partes
            iguales — un reparto uniforme parece un pronóstico y no lo es.
        """
        condition = self.condition_of(regime, volatility)
        counts = self._counts.get(condition, {})
        sample = sum(counts.values())
        if sample < self._settings.min_sample:
            return RegimeForecast(
                symbol=symbol,
                at=utc_now(),
                condition=condition,
                horizon_bars=self._settings.horizon_bars,
                reason=f"muestra {sample} < mínimo {self._settings.min_sample} para '{condition}'",
                sample=sample,
            )
        probabilities = _smoothed(counts, self._settings.smoothing)
        return RegimeForecast(
            symbol=symbol,
            at=utc_now(),
            condition=condition,
            horizon_bars=self._settings.horizon_bars,
            probabilities=probabilities,
            # La confianza satura con la muestra de referencia: es una medida de
            # cuánta evidencia hay detrás, NO de si el pronóstico acierta. Eso
            # lo dice el Brier, y confundir ambas cosas es el error clásico.
            confidence=min(1.0, sample / max(1, self._settings.confidence_sample)),
            sample=sample,
            observable=True,
        )

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    def track(self, forecast: RegimeForecast, anchor_index: int) -> None:
        """Remember a forecast so it can be scored once resolved.

        Args:
            forecast: Pronóstico emitido.
            anchor_index: Índice de la vela desde la que se pronosticó.
        """
        if not forecast.observable:
            return
        pending = self._pending.setdefault(
            forecast.symbol, deque(maxlen=self._settings.max_pending)
        )
        pending.append(
            _Pending(
                symbol=forecast.symbol,
                at=forecast.at,
                condition=forecast.condition,
                probabilities=dict(forecast.probabilities),
                anchor_index=anchor_index,
            )
        )

    def resolve(self, symbol: str, candles: list[Candle]) -> int:
        """Score every pending forecast whose horizon has already elapsed.

        Cada pronóstico resuelto se aprende además como observación nueva: el
        motor mejora con el tiempo sin ningún reentrenamiento explícito.

        Args:
            symbol: Activo.
            candles: Serie cronológica actual.

        Returns:
            Cuántos pronósticos se resolvieron.
        """
        pending = self._pending.get(symbol)
        if not pending:
            return 0
        horizon = self._settings.horizon_bars
        resolved = 0
        remaining: deque[_Pending] = deque(maxlen=self._settings.max_pending)
        for item in pending:
            outcome = classify_outcome(candles, item.anchor_index, horizon)
            if outcome is None:
                remaining.append(item)
                continue
            self._resolved.append((item.probabilities, outcome))
            self.learn(item.condition, outcome)
            resolved += 1
        self._pending[symbol] = remaining
        return resolved

    def score(self) -> ForecastScore:
        """Measure forecast quality against the trivial forecast.

        Returns:
            El marcador. Sin pronósticos resueltos devuelve todo a ``None``:
            un motor que no ha sido puesto a prueba no puede reportar calidad.
        """
        if not self._resolved:
            return ForecastScore()
        baseline = _smoothed(self._global, self._settings.smoothing)
        brier = 0.0
        baseline_brier = 0.0
        hits = 0
        for probabilities, outcome in self._resolved:
            brier += _brier(probabilities, outcome)
            baseline_brier += _brier(baseline, outcome)
            if probabilities and max(probabilities, key=lambda k: probabilities[k]) == outcome:
                hits += 1
        count = len(self._resolved)
        brier /= count
        baseline_brier /= count
        return ForecastScore(
            resolved=count,
            brier=brier,
            baseline_brier=baseline_brier,
            # Skill negativo se publica igual: es la señal de que este
            # pronóstico no vale para decidir nada, y esconderla sería peor
            # que no tener pronóstico.
            skill=None if baseline_brier <= 0 else 1.0 - brier / baseline_brier,
            hit_rate=hits / count,
        )

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "horizon_bars": self._settings.horizon_bars,
            "conditions": {
                condition: dict(counts)
                for condition, counts in sorted(self._counts.items())
                if sum(counts.values()) > 0
            },
            "observations": sum(self._global.values()),
            "pending": {symbol: len(items) for symbol, items in sorted(self._pending.items())},
            "score": self.score().to_dict(),
        }


# ---------------------------------------------------------------------------
# Clasificación del desenlace
# ---------------------------------------------------------------------------


def classify_outcome(candles: list[Candle], index: int, horizon: int) -> str | None:
    """Classify what happened in the ``horizon`` bars after ``index``.

    Reglas, en orden de prioridad (el primero que se cumple gana, para que la
    clasificación sea exhaustiva y excluyente):

    1. **breakout** — el precio sale del rango previo por encima o por debajo.
    2. **reversal** — se mueve en contra de la dirección previa más que el
       propio rango previo.
    3. **expansion** / **compression** — el rango de la ventana futura es
       claramente mayor o menor que el previo.
    4. **continuation** — el resto: nada notable, que es lo que más pasa.

    Args:
        candles: Serie cronológica.
        index: Vela desde la que se mira.
        horizon: Velas hacia adelante.

    Returns:
        El desenlace, o ``None`` si no hay velas suficientes a un lado u otro.
    """
    if index < horizon or index + horizon >= len(candles):
        return None
    past = candles[index - horizon : index + 1]
    future = candles[index + 1 : index + 1 + horizon]
    if not past or not future:
        return None

    past_high = max(c.high for c in past)
    past_low = min(c.low for c in past)
    past_range = past_high - past_low
    if past_range <= 0:
        return None
    future_high = max(c.high for c in future)
    future_low = min(c.low for c in future)
    future_range = future_high - future_low
    entry = candles[index].close
    exit_price = future[-1].close
    direction = candles[index].close - past[0].close

    if future_high > past_high or future_low < past_low:
        return "breakout"
    move = exit_price - entry
    if direction != 0 and (move * direction) < 0 and abs(move) > past_range * 0.5:
        return "reversal"
    if future_range > past_range * 1.5:
        return "expansion"
    if future_range < past_range * 0.5:
        return "compression"
    return "continuation"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _smoothed(counts: dict[str, int], smoothing: float) -> dict[str, float]:
    """Laplace-smoothed distribution over :data:`OUTCOMES`.

    El suavizado impide que un desenlace nunca visto reciba probabilidad 0.
    Con probabilidad 0 el Brier castiga infinitamente la primera vez que ocurra,
    y peor aún: el motor afirmaría que algo es imposible por no haberlo visto en
    unos cientos de observaciones.
    """
    total = sum(counts.get(outcome, 0) for outcome in OUTCOMES)
    denominator = total + smoothing * len(OUTCOMES)
    if denominator <= 0:
        return {outcome: 1.0 / len(OUTCOMES) for outcome in OUTCOMES}
    return {outcome: (counts.get(outcome, 0) + smoothing) / denominator for outcome in OUTCOMES}


def _brier(probabilities: dict[str, float], outcome: str) -> float:
    """Multiclass Brier score of one forecast against its realized outcome."""
    total = 0.0
    for candidate in OUTCOMES:
        predicted = probabilities.get(candidate, 0.0)
        actual = 1.0 if candidate == outcome else 0.0
        total += (predicted - actual) ** 2
    return total
