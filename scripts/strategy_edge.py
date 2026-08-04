"""Expectativa de cada estrategia AISLADA, sobre histórico real de exchange.

Hasta ahora el edge se ha medido siempre sobre el portfolio agregado ("el motor
da -0.078R"). Eso no distingue dos situaciones opuestas: *todas pierden un poco*
frente a *unas pocas pierden mucho y tapan a las que ganan*. La primera dice que
hay que rehacer el enfoque; la segunda, que hay que podar el catálogo.

El journal de producción no puede responderlo — sólo 130 operaciones llevan
atribución de estrategia, repartidas entre 1 y 44 por estrategia. El laboratorio
sí: este script corre **cada estrategia sola**, desactivando las otras 19, sobre
las mismas velas 1m reales, y compara su expectativa individual.

Con una única estrategia activa el consenso es unánime por construcción
(`min_agreement` se satisface siempre), así que lo que se mide es la señal de esa
estrategia pasando por los mismos filtros, sizing y Execution Engine que en vivo.

Uso:
    python scripts/strategy_edge.py BTCUSDT --bars 10000 --spread-bps 1.6

**No opera y no toca producción**: clona los settings por estrategia.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtesting.quant_source import run_quantcore_backtest
from app.config.settings import QuantStrategySettings, get_settings
from app.engine.plugins import PluginLoader
from scripts.calibrate_stops import fetch_candles


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="Par de Binance, p. ej. BTCUSDT")
    parser.add_argument("--bars", type=int, default=10_000)
    parser.add_argument("--spread-bps", type=float, required=True)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--only", help="Coma-separada: sólo estas estrategias")
    args = parser.parse_args()

    for noisy in ("app.execution", "app.engine", "app.backtesting"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    base = get_settings()
    names = sorted(c.name for c in PluginLoader(list(base.quant.plugin_dirs)).discover())
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        names = [n for n in names if n in wanted]

    print(f"Descargando {args.bars} velas 1m de {args.symbol}…")
    candles = fetch_candles(args.symbol, args.bars)
    span = f"{candles[0].start:%Y-%m-%d %H:%M} -> {candles[-1].end:%Y-%m-%d %H:%M}"
    print(f"  {len(candles)} velas  {span}")
    print(f"\nCada fila = esa estrategia SOLA (las otras {len(names) - 1} desactivadas).\n")

    header = f"{'estrategia':26s} {'trades':>7s} {'WR%':>6s} {'PF':>6s} {'exp R':>8s}"
    print(f"{header} {'ret%':>8s} | {'PF s/spr':>9s}")
    print("-" * 78)

    rows: list[tuple[str, float, int]] = []
    for name in names:
        trial = base.model_copy(deep=True)
        # Aislar: sólo esta estrategia queda habilitada.
        for other in names:
            cfg = trial.quant.strategies.get(other) or QuantStrategySettings()
            cfg = cfg.model_copy(update={"enabled": other == name})
            trial.quant.strategies[other] = cfg
        try:
            res = run_quantcore_backtest(
                trial, args.symbol, candles, spread_bps=args.spread_bps, balance=args.balance
            )
        except Exception as exc:  # una estrategia rota no corta el barrido
            print(f"{name:26s} error: {exc!r}")
            continue
        trades = int(res["trades"])
        exp = float(res["expectancy_r"])
        rows.append((name, exp, trades))
        mark = " <<<" if exp > 0 and trades >= 10 else ""
        print(
            f"{name:26s} {trades:7} {float(res['win_rate_pct']):6.1f} "
            f"{float(res['profit_factor']):6.2f} {exp:+8.3f} "
            f"{float(res['return_pct']):+8.2f} | "
            f"{float(res['zero_spread_profit_factor']):9.2f}{mark}"
        )

    print("\n--- resumen ---")
    con_muestra = [r for r in rows if r[2] >= 10]
    positivas = [r for r in con_muestra if r[1] > 0]
    print(f"  estrategias con >=10 operaciones: {len(con_muestra)}/{len(rows)}")
    print(f"  de esas, con expectativa positiva: {len(positivas)}")
    for name, exp, trades in sorted(positivas, key=lambda r: -r[1]):
        print(f"    {name:26s} {exp:+.3f}R  (n={trades})")
    if not positivas:
        print("    ninguna.")
    print("\nNada aplicado: cada fila usa un clon de los settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
