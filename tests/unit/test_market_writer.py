"""MarketDataWriter: buffering, flush y spill a disco cuando la DB cae."""

import json
from datetime import UTC, datetime
from pathlib import Path

from app.config.settings import DatabaseSettings, MarketStorageSettings
from app.database.engine import DatabaseManager
from app.market.models import Candle, Timeframe, Trade, TradeSide
from app.market.storage import MarketDataWriter

_TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _trade() -> Trade:
    return Trade(
        symbol="BTCUSDT",
        provider="binance",
        price=100.0,
        size=1.0,
        side=TradeSide.BUY,
        exchange_ts=_TS,
        local_ts=_TS,
    )


def _candle(closed: bool = True) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        provider="binance",
        timeframe=Timeframe.M1,
        start=_TS,
        end=_TS.replace(minute=1),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        closed=closed,
    )


def _writer(tmp_path: Path, *, enabled: bool = True) -> MarketDataWriter:
    # Puerto 1: conexión rechazada al instante → simula DB caída.
    db = DatabaseManager(DatabaseSettings(host="127.0.0.1", port=1, name="nope"))
    settings = MarketStorageSettings(
        enabled=enabled,
        flush_interval_seconds=3600.0,  # el test hace flush manual
        batch_size=100,
        spill_dir=tmp_path / "spill",
    )
    return MarketDataWriter(db, settings)


def test_buffers_trades_and_closed_candles(tmp_path: Path):
    writer = _writer(tmp_path)
    writer.add_trade(_trade())
    writer.add_candles([_candle(), _candle(closed=False)])  # la abierta se ignora
    status = writer.status()
    assert status["pending_ticks"] == 1
    assert status["pending_candles"] == 1


def test_disabled_writer_ignores_everything(tmp_path: Path):
    writer = _writer(tmp_path, enabled=False)
    writer.add_trade(_trade())
    writer.add_candles([_candle()])
    assert writer.status()["pending_ticks"] == 0
    assert writer.status()["pending_candles"] == 0


async def test_flush_spills_to_disk_when_db_is_down(tmp_path: Path):
    writer = _writer(tmp_path)
    writer.add_trade(_trade())
    writer.add_candles([_candle()])

    await writer.flush()

    status = writer.status()
    assert status["db_available"] is False
    assert status["rows_spilled"] == 2
    assert status["pending_ticks"] == 0

    spill_files = sorted((tmp_path / "spill").glob("*.jsonl"))
    assert len(spill_files) == 2  # ticks-*.jsonl y candles-*.jsonl
    tick_lines = [
        json.loads(line)
        for line in next(f for f in spill_files if "ticks" in f.name)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert tick_lines[0]["symbol"] == "BTCUSDT"


async def test_lifecycle_start_stop_flushes(tmp_path: Path):
    writer = _writer(tmp_path)
    await writer.start()
    writer.add_trade(_trade())
    await writer.stop()  # el stop drena el buffer (al spill, sin DB)
    assert writer.status()["rows_spilled"] == 1
