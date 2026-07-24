"""Optimización walk-forward de la selectividad de entrada (se corre en la VPS).

Busca los umbrales de entrada (``min_score``/``min_confidence``/``min_agreement``)
que generalizan: optimiza en un tramo IN-SAMPLE y valida en el siguiente tramo
OUT-OF-SAMPLE que NO vio el optimizador. Es la forma disciplinada de buscar edge
sin sobreoptimizar — si ni el mejor ajuste out-of-sample es rentable, es evidencia
de que no hay edge que extraer con estas estrategias.

Uso (en la VPS):
    python -m scripts.optimize_quantcore XAUUSDM --bars 20000 --spread-bps 0.6

No opera, no habilita live: sólo simula sobre histórico.
"""

from __future__ import annotations

import argparse
import logging

from app.backtesting.api import BacktestLab
from app.backtesting.mt5_history import pull_candles
from app.backtesting.optimizer import ParameterSpace
from app.backtesting.quant_source import make_quant_source_factory
from app.config.settings import get_settings
from app.market.models import Candle


def _pull(symbol: str, bars: int) -> list[Candle]:
    import MetaTrader5 as mt5  # noqa: N813  # sólo existe en la VPS

    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize() falló: {mt5.last_error()}")
    try:

        def _resolve(sym: str) -> str:
            for cand in (sym, sym.lower(), sym[:-1] + sym[-1].lower()):
                if mt5.symbol_info(cand) is not None:
                    return cand
            return sym

        return pull_candles(mt5, symbol, bars, resolve=_resolve)
    finally:
        mt5.shutdown()


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Walk-forward del QuantCore sobre histórico MT5")
    parser.add_argument("symbol")
    parser.add_argument("--bars", type=int, default=20000)
    parser.add_argument("--spread-bps", type=float, default=0.6)
    parser.add_argument("--train", type=int, default=4000, help="Velas in-sample por pliegue")
    parser.add_argument("--test", type=int, default=1500, help="Velas out-of-sample por pliegue")
    args = parser.parse_args()

    logging.getLogger("app.execution").setLevel(logging.ERROR)
    settings = get_settings()
    settings.quant.enabled = True

    print(f"Jalando {args.bars} velas 1m de {args.symbol}...")
    candles = _pull(args.symbol, args.bars)
    print(f"  {len(candles)} velas: {candles[0].start:%Y-%m-%d} → {candles[-1].start:%Y-%m-%d}")

    space = (
        ParameterSpace()
        .add_choices("min_score", [55.0, 60.0, 65.0, 70.0, 75.0])
        .add_choices("min_confidence", [0.5, 0.6, 0.7, 0.8])
        .add_choices("min_agreement", [0.5, 0.6])
    )
    factory = make_quant_source_factory(settings, args.symbol, spread_bps=args.spread_bps)
    lab = BacktestLab(settings)
    config = lab.make_config(
        args.symbol, timeframe="1m", label=f"{args.symbol.lower()}-wf", spread_bps=args.spread_bps
    )

    print("Corriendo walk-forward (grid, objetivo=profit_factor)...")
    print("  Esto tarda: varias combinaciones × varios pliegues × backtest completo.")
    report = lab.run_walk_forward(
        candles, space, factory, config, method="grid", objective="profit_factor"
    )

    print("\n===== WALK-FORWARD =====")
    print(f"  Pliegues:                {len(report.folds)}")
    print(f"  Retorno OOS total:       {report.total_out_of_sample_return_pct}%")
    print(f"  Retorno OOS promedio:    {report.average_out_of_sample_return_pct}%")
    print(f"  Pliegues OOS positivos:  {report.positive_fold_ratio * 100:.0f}%")
    print(f"  Estabilidad:             {report.stability}")
    print(f"  Estable (no overfit):    {'SI' if report.is_stable else 'NO'}")
    print("  --- por pliegue (out-of-sample) ---")
    for i, fold in enumerate(report.folds, 1):
        pf = fold.out_of_sample_stats.get("profit_factor")
        print(
            f"  #{i}: params={fold.best_params} → OOS retorno={fold.out_of_sample_return_pct}% "
            f"PF={pf}"
        )
    print("========================")
    if report.average_out_of_sample_return_pct <= 0:
        print(
            "\n⚠  Ni el mejor ajuste out-of-sample es rentable: no hay edge que extraer "
            "con estas estrategias en 1m. El siguiente paso lógico es cambiar de timeframe "
            "o de familia de estrategias, no seguir optimizando."
        )


if __name__ == "__main__":
    main()
