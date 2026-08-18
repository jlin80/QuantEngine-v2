"""Tabla de decisión del A/B de la salida por régimen.

El valor del experimento está en que el veredicto lo calcula el código a partir
de un criterio escrito antes de ver los números. Eso solo sirve si la tabla está
cubierta: estos tests fijan cada rama para que un cambio de umbral tenga que ser
deliberado y visible en el diff, no un ajuste silencioso que elija el veredicto.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.regime_exit_ab import (
    MAX_REGIME_EXIT_PCT_TREATED,
    MIN_TRADES_PER_ARM,
    _verdict,
)


def _arm(
    *,
    trades: int = 500,
    ci_low: float = -0.12,
    ci_high: float = -0.04,
    regime_pct: float = 0.0,
) -> dict[str, Any]:
    return {
        "trades": trades,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "exit_mix": {"regime_change": regime_pct},
    }


def _diff(*, ci_low: float, ci_high: float) -> dict[str, Any]:
    return {"ci_low": ci_low, "ci_high": ci_high}


def test_small_sample_is_its_own_verdict_not_a_negative_result() -> None:
    """Muestra insuficiente no puede leerse como 'no hay diferencia'."""
    verdict, _ = _verdict(
        _arm(trades=MIN_TRADES_PER_ARM - 1),
        _arm(),
        _diff(ci_low=-0.01, ci_high=0.01),
    )
    assert verdict == "muestra_insuficiente"


def test_manipulation_check_runs_before_anything_else() -> None:
    """Si el toggle no surtió efecto, el resultado no es interpretable.

    Se comprueba **antes** que la expectativa: un brazo tratado que sigue
    cerrando por régimen no midió la hipótesis, por buenos que sean sus números.
    """
    verdict, _ = _verdict(
        _arm(),
        _arm(regime_pct=MAX_REGIME_EXIT_PCT_TREATED + 0.1, ci_low=0.05, ci_high=0.20),
        _diff(ci_low=0.05, ci_high=0.30),
    )
    assert verdict == "experimento_invalido"


def test_positive_difference_and_positive_arm_means_the_corpus_was_confounded() -> None:
    verdict, _ = _verdict(
        _arm(),
        _arm(ci_low=0.02, ci_high=0.18),
        _diff(ci_low=0.03, ci_high=0.25),
    )
    assert verdict == "confundido_real"


def test_positive_difference_with_still_losing_arm_does_not_rescue_the_edge() -> None:
    """La rama que más fácil se sobreinterpretaría: mejora real, pero sigue perdiendo."""
    verdict, reading = _verdict(
        _arm(ci_low=-0.20, ci_high=-0.10),
        _arm(ci_low=-0.08, ci_high=-0.01),
        _diff(ci_low=0.02, ci_high=0.16),
    )
    assert verdict == "mejora_sin_edge"
    assert "NO rescata" in reading


def test_no_difference_and_negative_arm_closes_the_hypothesis() -> None:
    verdict, _ = _verdict(
        _arm(ci_low=-0.12, ci_high=-0.04),
        _arm(ci_low=-0.11, ci_high=-0.03),
        _diff(ci_low=-0.04, ci_high=0.05),
    )
    assert verdict == "edge_ausente_confirmado"


def test_inconclusive_arm_falls_back_to_no_difference() -> None:
    """Diferencia que toca cero y brazo tratado cuyo IC también lo toca."""
    verdict, _ = _verdict(
        _arm(ci_low=-0.12, ci_high=-0.04),
        _arm(ci_low=-0.06, ci_high=0.02),
        _diff(ci_low=-0.03, ci_high=0.07),
    )
    assert verdict == "sin_diferencia"
