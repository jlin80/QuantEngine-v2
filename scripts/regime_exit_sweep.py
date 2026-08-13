"""Compara configuraciones de la salida por régimen sobre histórico real de MT5.

La salida por cambio de régimen se lleva el 58-66% de los cierres en producción,
y el hueco entre lo que da la señal (hasta +2.6R en el Edge Research) y lo que
consigue la ejecución (+0.03R realizado) apunta ahí. Pero **medir lo que cuesta
no mide lo que evitó**: puede estar cortando pérdidas que no aparecen en ningún
sitio precisamente porque las cortó.

Este script corre las MISMAS velas por varias configuraciones y compara, para
cada una, expectativa, profit factor y —lo que de verdad importa— la mezcla de
motivos de salida. Una configuración que suba la expectativa moviendo cierres de
`regime_change` a `take_profit` es una mejora; una que la suba dejando la mezcla
igual es ruido de muestra.

Se corre en la VPS, en su propio proceso: abre y cierra su conexión MT5 y no
toca el motor vivo. No opera, no envía órdenes y no habilita live.

Uso:
    python -m scripts.regime_exit_sweep XAUUSDM --bars 20000 --spread-bps 0.6
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
from app.config.settings import Settings, get_settings
from app.market.models import Candle

# Cada escenario muta `settings.execution`. El orden importa solo para leer la
# tabla: `actual` va primero para que todo lo demas se compare contra el que
# esta corriendo ahora mismo en produccion.
SCENARIOS: dict[str, dict[str, Any]] = {
    # `actual` = lo que corre en produccion AHORA, con los overrides del Config
    # Center ya aplicados (ver `_base_settings`). Sin eso el baseline usaria los
    # defaults del codigo y la comparacion no diria nada sobre el bot real.
    "actual": {},
    # Menos agresiva: dar mas margen antes de permitir la salida.
    "holding_30min": {"regime_change_min_holding_seconds": 1800.0},
    "holding_60min": {"regime_change_min_holding_seconds": 3600.0},
    "confirmar_3": {"regime_exit_confirmations": 3},
    "confirmar_4": {"regime_exit_confirmations": 4},
    "sin_salida_regimen": {"exit_on_regime_change": False},
    # Controles en la direccion CONTRARIA (mas agresiva). Si la expectativa cae
    # aqui y sube en los de arriba, el efecto es del mecanismo; si se mueve al
    # azar en ambos sentidos, es ruido de muestra. Sin controles no se distingue.
    "ctrl_confirmar_1": {"regime_exit_confirmations": 1},
    "ctrl_todo_regimen": {"regime_exit_family_only": False},
}


def _base_settings() -> Settings:
    """Settings tal y como los ve el motor vivo: `.env` + overrides del Config Center.

    El motor aplica `config_store.reapply()` al arrancar, asi que los overrides
    persistidos (p. ej. `regime_change_min_holding_seconds=900`) forman parte de
    la configuracion real. Un script que solo lea `get_settings()` compararia
    contra los defaults del codigo y llamaria "actual" a algo que no corre.
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


def _run(
    base: Settings, symbol: str, candles: list[Candle], spread_bps: float, changes: dict[str, Any]
) -> dict[str, Any]:
    """Run one scenario over the candles and summarise it.

    Cada escenario parte de una copia profunda: sin ella, el primer escenario
    contaminaria a los siguientes y la comparacion no mediria nada.
    """
    trial = base.model_copy(deep=True)
    trial.quant.enabled = True
    for field, value in changes.items():
        setattr(trial.execution, field, value)

    lab = BacktestLab(trial)
    source = QuantCoreDecisionSource(trial, symbol, spread_bps=spread_bps)
    try:
        config = lab.make_config(
            symbol,
            timeframe="1m",
            label=f"{symbol.lower()}-regime-sweep",
            spread_bps=spread_bps,
        )
        result = lab.run_backtest(candles, source, config)
    finally:
        source.close()

    stats = result.statistics
    exits = Counter(str(getattr(t, "exit_reason", "?")) for t in result.trades)
    total = max(1, len(result.trades))
    return {
        "trades": len(result.trades),
        "expectancy_r": round(float(stats.get("expectancy_r", 0.0) or 0.0), 4),
        "profit_factor": round(float(stats.get("profit_factor", 0.0) or 0.0), 3),
        "win_rate_pct": round((float(stats.get("win_rate", 0.0) or 0.0)) * 100.0, 1),
        "max_drawdown_pct": round(float(stats.get("max_drawdown_pct", 0.0) or 0.0), 2),
        "return_pct": round(result.return_pct, 3),
        "exit_mix": {reason: round(100.0 * n / total, 1) for reason, n in exits.most_common()},
    }


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--bars", type=int, default=20000)
    parser.add_argument("--spread-bps", type=float, default=0.6)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    logging.getLogger("app.execution").setLevel(logging.ERROR)
    logging.getLogger("app.engine").setLevel(logging.ERROR)

    base = _base_settings()
    print(
        "Baseline: holding_min="
        f"{base.execution.regime_change_min_holding_seconds}s  "
        f"confirmaciones={base.execution.regime_exit_confirmations}  "
        f"solo_familia={base.execution.regime_exit_family_only}  "
        f"exit_on_regime={base.execution.exit_on_regime_change}"
    )
    print(f"Jalando {args.bars} velas 1m de {args.symbol}...", flush=True)
    candles = _pull_mt5_candles(args.symbol, args.bars)
    print(f"  {len(candles)} velas: {candles[0].start} -> {candles[-1].start}\n", flush=True)

    results: dict[str, dict[str, Any]] = {}
    for name, changes in SCENARIOS.items():
        print(f"  corriendo {name}...", flush=True)
        results[name] = _run(base, args.symbol, candles, args.spread_bps, changes)

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    print(
        f"\n{'escenario':<24} {'ops':>6} {'exp R':>9} "
        f"{'PF':>7} {'WR%':>6} {'DD%':>7} {'ret%':>8}"
    )
    print("-" * 72)
    for name, row in results.items():
        print(
            f"{name:<24} {row['trades']:>6} {row['expectancy_r']:>+9.4f} "
            f"{row['profit_factor']:>7.2f} {row['win_rate_pct']:>6.1f} "
            f"{row['max_drawdown_pct']:>7.2f} {row['return_pct']:>+8.2f}"
        )

    print("\nMezcla de salidas (% de operaciones):")
    reasons = sorted({r for row in results.values() for r in row["exit_mix"]})
    print(f"{'escenario':<24} " + " ".join(f"{r[:13]:>14}" for r in reasons))
    print("-" * (24 + 15 * len(reasons)))
    for name, row in results.items():
        cells = " ".join(f"{row['exit_mix'].get(r, 0.0):>13.1f}%" for r in reasons)
        print(f"{name:<24} {cells}")

    print(
        "\nLeer con cuidado: una expectativa mejor con la MISMA mezcla de salidas es"
        "\nruido de muestra. La mejora real se ve cuando los cierres se mueven de"
        "\n`regime_change` a niveles propios de la estrategia (take_profit/stop_loss)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
