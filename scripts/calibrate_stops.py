"""Barrido de calibración de stops/objetivos sobre histórico real de exchange.

Contexto (auditoría del 2026-08-04): el `atr_stop_multiplier` de 1.5 **nunca se
aplica en producción**. La distancia del stop sale siempre de uno de los dos
pisos (`min_stop_pct` o `min_stop_spread_multiple`), que se añadieron para que
el ruido del spread no barriera el stop. El efecto colateral es que el stop deja
de ser adaptativo y el objetivo (stop × `reward_risk`) acaba a 4-8× ATR — una
distancia que el precio no recorre en un horizonte de 1m, así que casi nunca se
alcanza.

Este script mide, no opina: corre el **QuantCore real** (mismas estrategias,
consenso, filtros y Execution Engine que operan en vivo) sobre velas 1m reales
de Binance, barriendo las tres variables implicadas, y compara cada combinación
contra la configuración vigente.

Uso:
    python scripts/calibrate_stops.py BTCUSDT --bars 5000 --spread-bps 1.6

**No opera, no toca producción y no cambia ninguna configuración**: clona los
settings por combinación y descarta el clon. La decisión de aplicar algo es del
operador.

Aviso sobre los datos: Binance es spot y el motor opera CFD de Exness, que
cotiza ~10 bps por debajo con offset estable (ver `docs/orderflow_nativo.md`).
Para calibrar la *forma* de los niveles (múltiplos de ATR) eso es irrelevante;
para trasladar el resultado absoluto a la cuenta real, no lo es.
"""

import argparse
import json
import logging
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtesting.quant_source import run_quantcore_backtest
from app.config.settings import get_settings
from app.market.models import Candle, Timeframe

_BINANCE = "https://api.binance.com/api/v3/klines"


def fetch_candles(symbol: str, bars: int) -> list[Candle]:
    """Download `bars` closed 1m candles from Binance public REST.

    Args:
        symbol: Par spot (p. ej. ``BTCUSDT``).
        bars: Número de velas.

    Returns:
        Las velas, de más antigua a más reciente.
    """
    out: list[Candle] = []
    end: int | None = None
    while len(out) < bars:
        url = f"{_BINANCE}?symbol={symbol}&interval=1m&limit=1000"
        if end is not None:
            url += f"&endTime={end}"
        with urllib.request.urlopen(url, timeout=30) as handle:
            rows = json.loads(handle.read())
        if not rows:
            break
        chunk = [
            Candle(
                symbol=symbol,
                provider="binance",
                timeframe=Timeframe.M1,
                start=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
                end=datetime.fromtimestamp(row[6] / 1000, tz=UTC),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                trades=int(row[8]),
                buy_volume=float(row[9]),
                sell_volume=max(float(row[5]) - float(row[9]), 0.0),
                closed=True,
                source="provider",
            )
            for row in rows
        ]
        out = chunk + out
        end = rows[0][0] - 1
    return out[-bars:]


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="Par de Binance, p. ej. BTCUSDT")
    parser.add_argument("--bars", type=int, default=5000, help="Velas 1m (default 5000)")
    parser.add_argument("--spread-bps", type=float, required=True, help="Spread real del bróker")
    parser.add_argument("--balance", type=float, default=1000.0)
    args = parser.parse_args()

    logging.getLogger("app.execution").setLevel(logging.ERROR)
    logging.getLogger("app.engine").setLevel(logging.ERROR)

    print(f"Descargando {args.bars} velas 1m de {args.symbol}…")
    candles = fetch_candles(args.symbol, args.bars)
    span = f"{candles[0].start:%Y-%m-%d %H:%M} -> {candles[-1].end:%Y-%m-%d %H:%M}"
    print(f"  {len(candles)} velas  {span}")

    base = get_settings()
    vigente = (
        base.execution.sizing.min_stop_pct,
        base.execution.sizing.atr_stop_multiplier,
        base.execution.sizing.reward_risk,
    )

    # El barrido baja el piso porcentual para que el múltiplo de ATR pueda
    # mandar, y prueba objetivos alcanzables en el horizonte de 1m.
    grid = [
        vigente,
        (0.15, 1.5, 1.2),
        (0.08, 1.5, 1.5),
        (0.08, 1.5, 1.2),
        (0.05, 1.5, 1.5),
        (0.05, 1.5, 1.2),
        (0.05, 1.0, 1.5),
        (0.05, 2.0, 1.5),
        (0.03, 1.5, 1.5),
    ]

    head = f"\n{'min_stop%':>10s} {'ATRx':>5s} {'R:R':>5s} | {'trades':>7s} {'WR%':>6s}"
    print(f"{head} {'PF':>6s} {'exp R':>8s} {'ret%':>8s} | {'PF s/spr':>9s} {'ret% s/spr':>11s}")
    print("-" * 96)
    for min_stop_pct, atr_mult, rr in grid:
        trial = base.model_copy(deep=True)
        trial.execution.sizing.min_stop_pct = min_stop_pct
        trial.execution.sizing.atr_stop_multiplier = atr_mult
        trial.execution.sizing.reward_risk = rr
        try:
            res = run_quantcore_backtest(
                trial, args.symbol, candles, spread_bps=args.spread_bps, balance=args.balance
            )
        except Exception as exc:  # una combinación rota no corta el barrido
            print(f"{min_stop_pct:10.2f} {atr_mult:5.1f} {rr:5.2f} | error: {exc!r}")
            continue
        tag = "  <== VIGENTE" if (min_stop_pct, atr_mult, rr) == vigente else ""
        print(
            f"{min_stop_pct:10.2f} {atr_mult:5.1f} {rr:5.2f} | "
            f"{res['trades']:7} {res['win_rate_pct']:6.1f} "
            f"{res['profit_factor']:6.2f} {res['expectancy_r']:+8.3f} "
            f"{res['return_pct']:+8.2f} | {res['zero_spread_profit_factor']:11.2f} "
            f"{res['zero_spread_return_pct']:+13.2f}{tag}"
        )
    print("\nNada de esto se ha aplicado: cada fila usa un clon de los settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
