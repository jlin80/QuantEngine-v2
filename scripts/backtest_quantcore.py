"""Backtest del QuantCore real sobre histórico de MT5 (se corre en la VPS).

Jala N velas 1m históricas del terminal MT5 (mismas credenciales del ``.env``),
corre EXACTAMENTE las estrategias/consenso/filtros que operan en vivo sobre esa
serie con el spread real del broker, y reutiliza el Execution Engine (sizing,
stops, riesgo) del backtest. Imprime win rate, profit factor y expectativa —
la respuesta estadística de si la configuración actual tiene edge, ANTES de
seguir quemando trades demo.

Uso (en la VPS, dentro de C:\\Users\\MT5\\QuantEngineV2):
    python -m scripts.backtest_quantcore ETHUSDM --bars 5000 --spread-bps 5.3

No opera, no envía órdenes, no habilita live: sólo lee histórico y simula.
"""

from __future__ import annotations

import argparse
import logging

from app.backtesting.mt5_history import pull_candles
from app.backtesting.quant_source import run_quantcore_backtest
from app.config.settings import get_settings
from app.market.models import Candle


def _pull_mt5_candles(symbol: str, bars: int) -> list[Candle]:
    """Fetch ``bars`` closed 1m candles for ``symbol`` from the MT5 terminal."""
    import MetaTrader5 as mt5  # noqa: N813  # import perezoso: sólo existe en la VPS

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
    parser = argparse.ArgumentParser(description="Backtest del QuantCore real sobre histórico MT5")
    parser.add_argument("symbol", help="Símbolo, p. ej. ETHUSDM")
    parser.add_argument("--bars", type=int, default=5000, help="Velas 1m a jalar (default 5000)")
    parser.add_argument(
        "--spread-bps",
        type=float,
        default=5.3,
        help="Spread real del broker en bps (default 5.3, el de ETH en Exness)",
    )
    parser.add_argument("--balance", type=float, default=1000.0, help="Balance inicial")
    args = parser.parse_args()

    # Silencia el ruido de rechazos por riesgo (esperados en el backtest).
    logging.getLogger("app.execution").setLevel(logging.ERROR)

    settings = get_settings()
    settings.quant.enabled = True

    print(f"Jalando {args.bars} velas 1m de {args.symbol} desde MT5...")
    candles = _pull_mt5_candles(args.symbol, args.bars)
    span = f"{candles[0].start:%Y-%m-%d %H:%M} → {candles[-1].start:%Y-%m-%d %H:%M}"
    print(f"  {len(candles)} velas: {span}")

    print(f"Corriendo backtest (spread {args.spread_bps} bps + spread 0 de control)...")
    r = run_quantcore_backtest(
        settings, args.symbol, candles, spread_bps=args.spread_bps, balance=args.balance
    )
    print("\n===== RESULTADO (spread real) =====")
    print(f"  Trades:          {r['trades']}")
    print(f"  Win rate:        {r['win_rate_pct']}%")
    print(f"  Profit factor:   {r['profit_factor']}")
    print(f"  Expectativa (R): {r['expectancy_r']}")
    print(f"  Retorno neto:    {r['return_pct']}%")
    print(f"  Max drawdown:    {r['max_drawdown_pct']}%")
    print("----- control (spread 0) -----")
    print(f"  Trades:          {r['zero_spread_trades']}")
    print(f"  Win rate:        {r['zero_spread_win_rate_pct']}%")
    print(f"  Profit factor:   {r['zero_spread_profit_factor']}")
    print(f"  Retorno neto:    {r['zero_spread_return_pct']}%")
    print("===================================")
    if int(r["trades"]) < 30:
        print("\n⚠  Menos de 30 trades: muestra insuficiente para concluir. Sube --bars.")


if __name__ == "__main__":
    main()
