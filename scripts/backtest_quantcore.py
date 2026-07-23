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
from datetime import UTC, datetime, timedelta

from app.backtesting.api import BacktestLab
from app.backtesting.quant_source import QuantCoreDecisionSource
from app.config.settings import get_settings
from app.market.models import Candle, Timeframe


def _pull_mt5_candles(symbol: str, bars: int) -> list[Candle]:
    """Fetch ``bars`` closed 1m candles for ``symbol`` from the MT5 terminal."""
    import MetaTrader5 as mt5  # noqa: N813  # import perezoso: sólo existe en la VPS

    settings = get_settings()
    mt5cfg = settings.market  # credenciales MT5 viven en el feed/broker config
    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize() falló: {mt5.last_error()}")
    try:
        real = symbol
        info = mt5.symbol_info(symbol)
        if info is None:
            # el terminal expone XAUUSDm/ETHUSDm en otra caja
            for cand in (symbol.lower(), symbol[:-1] + symbol[-1].lower()):
                if mt5.symbol_info(cand) is not None:
                    real = cand
                    break
        rates = mt5.copy_rates_from_pos(real, mt5.TIMEFRAME_M1, 0, bars)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"copy_rates_from_pos sin datos para {real}: {mt5.last_error()}")
    finally:
        mt5.shutdown()

    candles: list[Candle] = []
    for row in rates:
        start = datetime.fromtimestamp(int(row["time"]), tz=UTC)
        candles.append(
            Candle(
                symbol=symbol.upper(),
                provider="mt5_history",
                timeframe=Timeframe.M1,
                start=start,
                end=start + timedelta(minutes=1),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["tick_volume"]),
                trades=int(row["tick_volume"]),
                closed=True,
                source="provider",
            )
        )
    _ = mt5cfg  # reservado si en el futuro se quiere loguear la config
    return candles


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

    lab = BacktestLab(settings)
    source = QuantCoreDecisionSource(settings, args.symbol, spread_bps=args.spread_bps)
    config = lab.make_config(
        args.symbol,
        timeframe="1m",
        label=f"{args.symbol.lower()}-quantcore",
        spread_bps=args.spread_bps,
        initial_balance=args.balance,
    )
    print(f"Corriendo backtest (spread {args.spread_bps} bps)...")
    result = lab.run_backtest(candles, source, config)
    source.close()

    st = result.statistics
    print("\n===== RESULTADO =====")
    print(f"  Trades:          {st.get('total_trades')}")
    print(f"  Win rate:        {(st.get('win_rate') or 0) * 100:.1f}%")
    print(f"  Profit factor:   {st.get('profit_factor')}")
    print(f"  Expectativa (R): {st.get('expectancy_r')}")
    print(f"  Retorno neto:    {result.return_pct:.3f}%")
    print(f"  Max drawdown:    {(st.get('max_drawdown_pct') or 0):.3f}%")
    print(f"  Sharpe:          {st.get('sharpe')}")
    print("=====================")
    if (st.get("total_trades") or 0) < 30:
        print("\n⚠  Menos de 30 trades: muestra insuficiente para concluir. Sube --bars.")


if __name__ == "__main__":
    main()
