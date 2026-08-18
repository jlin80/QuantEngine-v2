"""A/B de una sola variable: la salida por cambio de régimen, con criterio pre-registrado.

Por qué este script existiendo ya ``regime_exit_sweep.py``
----------------------------------------------------------

El sweep compara ocho configuraciones y deja la lectura al operador. Sirve para
explorar; no sirve para **decidir**, por tres razones:

1. Compara expectativas **puntuales**. Con ~1000 operaciones por brazo, una
   diferencia de 0.05R cabe entera dentro del ruido de muestreo. Elegir la fila
   con el número más alto es elegir ruido.
2. Ocho escenarios son ocho oportunidades de que uno parezca bueno por azar —
   el mismo problema de comparaciones múltiples que ``session_edge`` corrige con
   FDR y que el sweep no corrige.
3. No hay regla de decisión escrita antes de ver los números, así que cualquier
   resultado admite reinterpretación a posteriori.

Este script hace lo contrario: **dos brazos, una variable, un criterio impreso
antes de correr**. La pregunta que responde es la única cuya respuesta cambia
la decisión de fondo del proyecto:

    ¿La expectativa negativa medida (-0.078R) es ausencia de edge, o es el
    efecto de que `regime_change` cierra el 91.3 % de las posiciones a los
    ~4 minutos, antes de que ninguna estrategia llegue a probar su tesis?

Son hipótesis distintas y hasta ahora ningún experimento las ha separado: lo
medido en producción llevaba la salida por régimen puesta, y los barridos de
stops/objetivos corrieron con ``context=None``, es decir sobre un motor que
**no podía** cerrar por régimen. Nunca se comparó el mismo sistema consigo mismo.

Ahora sí se puede: ``market_context_enabled`` está activo (el backtest ve el
régimen real) y las comisiones del broker ya se leen del deal.

Se corre en la VPS, en su propio proceso: abre y cierra su conexión MT5 y no
toca el motor vivo. No opera, no envía órdenes y no habilita live.

Uso:
    python -m scripts.regime_exit_ab XAUUSDM --bars 50000 --spread-bps 0.6
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtesting.api import BacktestLab
from app.backtesting.mt5_history import pull_candles
from app.backtesting.quant_source import QuantCoreDecisionSource
from app.backtesting.session_edge import bootstrap_difference, bootstrap_expectancy
from app.config.settings import Settings, get_settings
from app.market.models import Candle

# ----------------------------------------------------------------------
# Criterio pre-registrado
#
# Estos umbrales se fijan AQUI, en el codigo, antes de la primera corrida, y se
# imprimen antes de los resultados. Cambiarlos despues de ver los numeros
# invalida el experimento: seria elegir la regla que da el veredicto preferido.
# ----------------------------------------------------------------------

MIN_TRADES_PER_ARM = 200
"""Muestra minima por brazo para que el veredicto cuente.

Por debajo, el IC de la diferencia es tan ancho que no excluye nada y el
resultado seria "no se sabe" disfrazado de "no hay diferencia". Muestra
insuficiente es una categoria de salida propia, no un fallo.
"""

MAX_REGIME_EXIT_PCT_TREATED = 5.0
"""Tope de salidas por `regime_change` que se tolera en el brazo tratado.

Chequeo de manipulacion (*manipulation check*): si se desactiva la salida por
regimen y aun asi domina la mezcla, el toggle no hizo lo que se cree y NADA de
lo que siga es interpretable. Se comprueba antes de mirar la expectativa.
"""

_THESIS_EXITS = frozenset({"take_profit", "stop_loss", "trailing_stop", "break_even"})


def _verdict(
    control: dict[str, Any], treated: dict[str, Any], diff: dict[str, Any]
) -> tuple[str, str]:
    """Aplica la tabla de decision pre-registrada. Devuelve ``(veredicto, lectura)``.

    El veredicto lo calcula el codigo, no el ojo: cada rama estaba escrita antes
    de existir los numeros que la disparan.
    """
    if control["trades"] < MIN_TRADES_PER_ARM or treated["trades"] < MIN_TRADES_PER_ARM:
        return (
            "muestra_insuficiente",
            f"Algun brazo no llega a {MIN_TRADES_PER_ARM} operaciones. El experimento "
            "no concluye: hacen falta mas velas, no una lectura mas benevola.",
        )

    regime_pct = treated["exit_mix"].get("regime_change", 0.0)
    if regime_pct > MAX_REGIME_EXIT_PCT_TREATED:
        return (
            "experimento_invalido",
            f"El brazo tratado sigue cerrando el {regime_pct:.1f} % por regimen pese a "
            "tener la salida desactivada. El toggle no surtio efecto; no se ha medido "
            "la hipotesis. Revisar el cableado antes de volver a correr.",
        )

    diff_positive = diff["ci_low"] > 0.0
    treated_positive = treated["ci_low"] > 0.0
    treated_negative = treated["ci_high"] < 0.0

    if diff_positive and treated_positive:
        return (
            "confundido_real",
            "Quitar la salida por regimen mejora la expectativa Y el brazo tratado es "
            "positivo con el IC excluyendo cero. Lo medido hasta ahora era el motor de "
            "ejecucion, no las estrategias: el corpus de evidencia hay que rehacerlo "
            "entero con esta configuracion.",
        )
    if diff_positive:
        return (
            "mejora_sin_edge",
            "La salida por regimen si estaba costando expectativa (la diferencia es "
            "positiva con IC excluyendo cero), pero el brazo tratado sigue sin ser "
            "rentable. Es un hallazgo real sobre la ejecucion y NO rescata la "
            "hipotesis de edge.",
        )
    if treated_negative:
        return (
            "edge_ausente_confirmado",
            "Sin la salida por regimen la expectativa sigue siendo negativa con el IC "
            "excluyendo cero. Desaparece el ultimo confundido conocido: las tres vias "
            "anteriores y esta miden ya el mismo sistema. La hipotesis de scalping 1m "
            "sobre CFD sin microestructura queda falsada, no aplazada.",
        )
    return (
        "sin_diferencia",
        "El IC de la diferencia contiene el cero: la salida por regimen no era el "
        "confundido que se sospechaba, y el veredicto original se sostiene sin "
        "necesitar esa explicacion.",
    )


def _base_settings() -> Settings:
    """Settings tal y como los ve el motor vivo: `.env` + overrides del Config Center.

    Misma razon que en ``regime_exit_sweep``: el motor aplica
    ``config_store.reapply()`` al arrancar, asi que los overrides persistidos
    forman parte de la configuracion real. Comparar contra los defaults del
    codigo seria llamar "actual" a algo que no corre en ninguna parte.
    """
    from app.dashboard.api.config_store import config_store

    settings = get_settings()
    config_store.reapply(settings)
    return settings


def _pull_mt5_candles(symbol: str, bars: int) -> list[Candle]:
    """Fetch ``bars`` closed 1m candles from the MT5 terminal (own connection)."""
    import MetaTrader5 as mt5  # noqa: N813  # solo existe en la VPS

    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize() fallo: {mt5.last_error()}")
    try:

        def _resolve(sym: str) -> str:
            for cand in (sym, sym.lower(), sym[:-1] + sym[-1].lower()):
                if mt5.symbol_info(cand) is not None:
                    return cand
            return sym

        return pull_candles(mt5, symbol, bars, resolve=_resolve)
    finally:
        mt5.shutdown()


def _run_arm(
    base: Settings,
    symbol: str,
    candles: list[Candle],
    spread_bps: float,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Corre un brazo sobre las MISMAS velas y devuelve sus metricas y R-multiplos.

    Copia profunda por brazo: sin ella el primero contaminaria al segundo y la
    comparacion no mediria la variable, mediria el orden de ejecucion.
    """
    trial = base.model_copy(deep=True)
    trial.quant.enabled = True
    for field, value in changes.items():
        setattr(trial.execution, field, value)

    lab = BacktestLab(trial)
    source = QuantCoreDecisionSource(trial, symbol, spread_bps=spread_bps)
    try:
        config = lab.make_config(
            symbol, timeframe="1m", label=f"{symbol.lower()}-regime-ab", spread_bps=spread_bps
        )
        result = lab.run_backtest(candles, source, config)
    finally:
        source.close()

    r_values = [float(t.r_multiple) for t in result.trades]
    exits = Counter(str(getattr(t, "exit_reason", "?")) for t in result.trades)
    total = max(1, len(result.trades))
    stats = result.statistics

    row: dict[str, Any] = {
        "trades": len(result.trades),
        "expectancy_r": round(sum(r_values) / len(r_values), 4) if r_values else 0.0,
        "profit_factor": round(float(stats.get("profit_factor", 0.0) or 0.0), 3),
        "max_drawdown_pct": round(float(stats.get("max_drawdown_pct", 0.0) or 0.0), 2),
        "commission_total": round(
            sum(float(getattr(t, "commission", 0.0)) for t in result.trades), 2
        ),
        "exit_mix": {reason: round(100.0 * n / total, 1) for reason, n in exits.most_common()},
        "thesis_exit_pct": round(
            100.0 * sum(n for r, n in exits.items() if r.lower() in _THESIS_EXITS) / total, 1
        ),
        "_r_values": r_values,
    }
    if r_values:
        ci_low, ci_high, p_value = bootstrap_expectancy(r_values)
        row |= {
            "ci_low": round(ci_low, 4),
            "ci_high": round(ci_high, 4),
            "p_value": round(p_value, 4),
        }
    else:
        row |= {"ci_low": 0.0, "ci_high": 0.0, "p_value": 1.0}
    return row


def _print_protocol() -> None:
    """Imprime el criterio ANTES de los numeros. Ese es el punto del script."""
    print("=" * 78)
    print("CRITERIO PRE-REGISTRADO (fijado en el codigo antes de esta corrida)")
    print("=" * 78)
    print(f"""
Variable unica:   execution.exit_on_regime_change   (control=True, tratado=False)
Muestra minima:   {MIN_TRADES_PER_ARM} operaciones por brazo
Chequeo previo:   el brazo tratado debe cerrar <= {MAX_REGIME_EXIT_PCT_TREATED:.0f} % por regimen
                  (si no, el toggle no surtio efecto y nada es interpretable)

Tabla de decision, sobre IC bootstrap al 95 %:

  confundido_real         diferencia > 0  Y  tratado > 0     -> rehacer el corpus
  mejora_sin_edge         diferencia > 0  Y  tratado <= 0    -> hallazgo de ejecucion
  edge_ausente_confirmado diferencia ~ 0  Y  tratado < 0     -> hipotesis falsada
  sin_diferencia          el IC de la diferencia toca cero   -> veredicto original

"Diferencia > 0" significa que el extremo INFERIOR del IC esta sobre cero, no que
la media puntual sea mayor.
""")


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--bars", type=int, default=50000)
    parser.add_argument("--spread-bps", type=float, default=0.6)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    logging.getLogger("app.execution").setLevel(logging.ERROR)
    logging.getLogger("app.engine").setLevel(logging.ERROR)

    _print_protocol()

    base = _base_settings()
    if not base.backtesting.market_context_enabled:
        print(
            "ABORTADO: `backtesting.market_context_enabled` esta en False, asi que el\n"
            "backtest no ve el regimen y el brazo de control no puede cerrar por esa via.\n"
            "Los dos brazos serian identicos y el experimento no mediria nada.",
            file=sys.stderr,
        )
        return 2

    print(f"Jalando {args.bars} velas 1m de {args.symbol}...", flush=True)
    candles = _pull_mt5_candles(args.symbol, args.bars)
    print(f"  {len(candles)} velas: {candles[0].start} -> {candles[-1].start}\n", flush=True)

    print("  corriendo control (con salida por regimen)...", flush=True)
    control = _run_arm(base, args.symbol, candles, args.spread_bps, {})
    print("  corriendo tratado (sin salida por regimen)...", flush=True)
    treated = _run_arm(
        base, args.symbol, candles, args.spread_bps, {"exit_on_regime_change": False}
    )

    if control["_r_values"] and treated["_r_values"]:
        d_low, d_high, d_p = bootstrap_difference(treated["_r_values"], control["_r_values"])
    else:
        d_low = d_high = d_p = 0.0
    diff = {
        "delta_r": round(treated["expectancy_r"] - control["expectancy_r"], 4),
        "ci_low": round(d_low, 4),
        "ci_high": round(d_high, 4),
        "p_value": round(d_p, 4),
    }

    verdict, reading = _verdict(control, treated, diff)

    for row in (control, treated):
        row.pop("_r_values", None)

    if args.json:
        print(
            json.dumps(
                {"control": control, "treated": treated, "difference": diff, "verdict": verdict},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(
        f"\n{'brazo':<12} {'ops':>6} {'exp R':>9} {'IC 95%':>22} {'PF':>7} {'DD%':>7} {'tesis%':>8}"
    )
    print("-" * 78)
    for name, row in (("control", control), ("tratado", treated)):
        ci = f"[{row['ci_low']:+.4f}, {row['ci_high']:+.4f}]"
        print(
            f"{name:<12} {row['trades']:>6} {row['expectancy_r']:>+9.4f} {ci:>22} "
            f"{row['profit_factor']:>7.2f} {row['max_drawdown_pct']:>7.2f} "
            f"{row['thesis_exit_pct']:>7.1f}%"
        )

    print(
        f"\ndiferencia (tratado - control): {diff['delta_r']:+.4f}R   "
        f"IC 95% [{diff['ci_low']:+.4f}, {diff['ci_high']:+.4f}]   p={diff['p_value']:.4f}"
    )
    print(
        f"comisiones cobradas: control {control['commission_total']:.2f}   "
        f"tratado {treated['commission_total']:.2f}"
    )
    if control["commission_total"] == 0.0 and treated["commission_total"] == 0.0:
        print(
            "  AVISO: comisiones a cero en ambos brazos. Si el dataset viene de un\n"
            "  broker que si cobra, la expectativa de los dos brazos esta sobrestimada."
        )

    print("\nMezcla de salidas (% de operaciones):")
    reasons = sorted(set(control["exit_mix"]) | set(treated["exit_mix"]))
    print(f"{'brazo':<12} " + " ".join(f"{r[:13]:>14}" for r in reasons))
    print("-" * (12 + 15 * len(reasons)))
    for name, row in (("control", control), ("tratado", treated)):
        cells = " ".join(f"{row['exit_mix'].get(r, 0.0):>13.1f}%" for r in reasons)
        print(f"{name:<12} {cells}")

    print("\n" + "=" * 78)
    print(f"VEREDICTO: {verdict}")
    print("=" * 78)
    print(reading)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
