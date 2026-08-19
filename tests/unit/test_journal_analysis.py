"""Segmentacion del journal real y su walk-forward.

Lo que estas herramientas deciden —que estrategia se desactiva, que celda se
promueve— acaba tocando produccion, asi que las dos trampas que evitan tienen
que estar fijadas por tests:

1. Las dimensiones solo pueden mirar cosas conocidas **al abrir**. Colar
   `exit_reason` o la duracion seria filtrar por el resultado.
2. La seleccion del walk-forward se hace con el in-sample y **no** puede
   volver a elegir mirando el out-of-sample.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.journal_segments import bucket_num, dimensions
from scripts.journal_walk_forward import dims, select


def _trade(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "symbol": "XAUUSDM",
        "strategy": "fair_value_gap",
        "strategy_category": "smc",
        "regime": "ranging",
        "volatility": "normal",
        "side": "long",
        "confidence": 0.9,
        "score": 75.0,
        "spread_bps": 0.6,
        "r_multiple": 0.5,
        "exit_time": "2026-08-01T00:00:00+00:00",
    }
    base.update(over)
    return base


# ----------------------------------------------------------------------
# Sin mirar el futuro
# ----------------------------------------------------------------------


def test_dimensions_never_expose_post_trade_information() -> None:
    """La trampa que hunde este analisis: segmentar por como acabo la operacion."""
    trade = _trade(exit_reason="stop_loss", duration_seconds=200.0, pnl=-3.0, is_win=False)

    keys = set(dimensions(trade)) | set(dims(trade))

    forbidden = {"exit_reason", "duration_seconds", "pnl", "is_win", "r_multiple", "exit_price"}
    assert keys.isdisjoint(forbidden)


def test_dimensions_only_read_fields_known_at_entry() -> None:
    trade = _trade()
    assert dimensions(trade)["strategy"] == "fair_value_gap"
    assert dimensions(trade)["regimen"] == "ranging"
    assert dimensions(trade)["lado"] == "long"


def test_missing_attribution_is_its_own_bucket_not_a_silent_drop() -> None:
    """202 operaciones del journal real no traen `strategy`: no pueden desaparecer."""
    assert dimensions(_trade(strategy=None))["strategy"] == "?"
    assert dims(_trade(strategy_category=None))["categoria"] == "?"


# ----------------------------------------------------------------------
# Buckets numericos
# ----------------------------------------------------------------------


def test_bucket_num_places_values_in_the_expected_side() -> None:
    assert bucket_num(0.5, [0.6, 0.75], "conf") == "conf<0.6"
    assert bucket_num(0.7, [0.6, 0.75], "conf") == "conf<0.75"
    assert bucket_num(0.9, [0.6, 0.75], "conf") == "conf>=0.75"


def test_bucket_num_keeps_missing_values_visible() -> None:
    assert bucket_num(None, [0.6], "conf") == "conf=?"


# ----------------------------------------------------------------------
# Seleccion del walk-forward
# ----------------------------------------------------------------------


def test_selection_is_empty_when_nothing_clears_the_bar() -> None:
    """Un conjunto de seleccion vacio es un resultado, no un fallo."""
    losers = [_trade(r_multiple=-0.5) for _ in range(80)]
    assert select(losers) == set()


def test_selection_ignores_cells_below_the_minimum_sample() -> None:
    """Diez operaciones ganadoras no promueven una celda por buenas que sean."""
    few = [_trade(strategy="unicornio", r_multiple=3.0) for _ in range(10)]
    assert not any(cell == ("strategy", "unicornio") for cell in select(few))


def test_selection_promotes_a_consistently_winning_cell() -> None:
    winners = [_trade(strategy="ganadora", r_multiple=1.0) for _ in range(60)]
    chosen = select(winners)
    assert ("strategy", "ganadora") in chosen


def test_selection_is_deterministic() -> None:
    """Dos corridas sobre el mismo journal tienen que elegir lo mismo."""
    rows = [_trade(r_multiple=1.0 if i % 3 else -0.6) for i in range(90)]
    assert select(rows) == select(rows)
