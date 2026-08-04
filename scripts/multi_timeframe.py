"""¿Aporta algo leer el régimen en un marco superior al de entrada?

El motor es hoy estrictamente monotimeframe: las 20 estrategias, el detector de
régimen, el Market Context y el Feature Store leen todos velas de 1m. Con el
`lookback` de 50 del detector, **toda su visión de mercado son 50 minutos**. No
hay sesgo de 1h, ni dirección de 15m, ni confluencia entre marcos — el
planteamiento estándar de intradía no está implementado.

Eso es una hipótesis plausible para el resultado de `strategy_edge.py`: media
biblioteca son conceptos de *estructura de mercado* (`bos`, `choch`, `mss`,
`order_block`, `fair_value_gap`), y la estructura leída en velas de 1 minuto se
"rompe" cada pocos minutos. Detectar estructura ahí es detectar ruido — lo que
explicaría que el ranking entre estrategias no se replique entre símbolos
(r = +0.084).

Este script mide esa hipótesis: mismo QuantCore, mismas estrategias en 1m para
el *timing*, y sólo se mueve el timeframe del **detector de régimen**. Las velas
superiores se construyen desde la misma serie de 1m y sólo se publican cerradas
(`app.backtesting.htf`), así que no hay lookahead.

Uso:
    python scripts/multi_timeframe.py BTCUSDT --bars 10000 --spread-bps 1.6

**No opera y no toca producción**: clona los settings por combinación.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtesting.api import BacktestLab
from app.backtesting.quant_source import QuantCoreDecisionSource
from app.config.settings import Settings, get_settings
from app.market.models import Candle, Timeframe
from scripts.calibrate_stops import fetch_candles

# Régimen leído en cada uno de estos marcos. El lookback se ajusta para que la
# ventana temporal sea comparable y no se confunda "más contexto" con "más
# velas": 50x1m = 50 min; 20x15m = 5 h; 12x1h = 12 h.
ESCENARIOS: list[tuple[str, int]] = [
    ("1m", 50),
    ("5m", 36),
    ("15m", 20),
    ("15m", 40),
    ("1h", 12),
    ("1h", 24),
]


def _run(
    base: Settings,
    symbol: str,
    candles: list[Candle],
    *,
    regime_tf: str,
    lookback: int,
    spread_bps: float,
    balance: float,
) -> dict[str, float | int | str]:
    """One backtest with the regime detector on `regime_tf`."""
    trial = base.model_copy(deep=True)
    trial.quant.regime.timeframe = regime_tf
    trial.quant.regime.lookback = lookback

    higher = () if regime_tf == "1m" else (Timeframe(regime_tf),)
    lab = BacktestLab(trial)

    def one(spread: float) -> dict[str, float | int]:
        source = QuantCoreDecisionSource(trial, symbol, spread_bps=spread, higher_timeframes=higher)
        config = lab.make_config(
            symbol,
            timeframe="1m",
            label=f"{symbol.lower()}-regime-{regime_tf}-{lookback}",
            spread_bps=spread,
            initial_balance=balance,
        )
        result = lab.run_backtest(candles, source, config)
        st = result.statistics
        return {
            "trades": int(st.get("total_trades", 0) or 0),
            "win_rate_pct": round((st.get("win_rate", 0.0) or 0.0) * 100.0, 1),
            "profit_factor": round(float(st.get("profit_factor", 0.0) or 0.0), 3),
            "expectancy_r": round(float(st.get("expectancy_r", 0.0) or 0.0), 3),
            "return_pct": round(result.return_pct, 3),
        }

    real = one(spread_bps)
    return {**real, "zero_pf": one(0.0)["profit_factor"]}


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--bars", type=int, default=10_000)
    parser.add_argument("--spread-bps", type=float, required=True)
    parser.add_argument("--balance", type=float, default=1000.0)
    args = parser.parse_args()

    for noisy in ("app.execution", "app.engine", "app.backtesting"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    print(f"Descargando {args.bars} velas 1m de {args.symbol}…")
    candles = fetch_candles(args.symbol, args.bars)
    span = f"{candles[0].start:%Y-%m-%d %H:%M} -> {candles[-1].end:%Y-%m-%d %H:%M}"
    print(f"  {len(candles)} velas  {span}")
    print("\nEntrada siempre en 1m; sólo cambia el marco del detector de régimen.\n")

    base = get_settings()
    head = f"{'régimen':>10s} {'lookback':>9s} {'ventana':>9s} | {'trades':>7s} {'WR%':>6s}"
    print(f"{head} {'PF':>6s} {'exp R':>8s} {'ret%':>8s} | {'PF s/spr':>9s}")
    print("-" * 88)

    for regime_tf, lookback in ESCENARIOS:
        seconds = Timeframe(regime_tf).seconds or 60
        window = f"{seconds * lookback / 3600:.1f}h"
        try:
            res = _run(
                base,
                args.symbol,
                candles,
                regime_tf=regime_tf,
                lookback=lookback,
                spread_bps=args.spread_bps,
                balance=args.balance,
            )
        except Exception as exc:
            print(f"{regime_tf:>10s} {lookback:9} {window:>9s} | error: {exc!r}")
            continue
        tag = "  <== VIGENTE" if (regime_tf, lookback) == ("1m", 50) else ""
        print(
            f"{regime_tf:>10s} {lookback:9} {window:>9s} | "
            f"{res['trades']:7} {float(res['win_rate_pct']):6.1f} "
            f"{float(res['profit_factor']):6.2f} {float(res['expectancy_r']):+8.3f} "
            f"{float(res['return_pct']):+8.2f} | {float(res['zero_pf']):9.2f}{tag}"
        )

    print("\nNada aplicado: cada fila usa un clon de los settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
