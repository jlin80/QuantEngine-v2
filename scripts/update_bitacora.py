"""Registra el hito de cierre de una fase usando el DocumentationService.

Uso: python scripts/update_bitacora.py [fase]   (default: la más reciente)
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.documentation.backends import MarkdownJournalBackend
from app.documentation.service import DocumentationService

_ROOT = Path(__file__).resolve().parents[1]

_FASE_2 = """\
Se completó la Fase 2 del proyecto: Data Engine profesional, única fuente de
verdad de datos de mercado. Ningún módulo se conecta a un broker directamente.

**Entregado:**
- Proveedores Binance, Bybit y OKX funcionales (WS + REST de reconstrucción);
  Bitget, OANDA, MetaTrader 5 e IBKR preparados tras la misma interfaz
  (`ProviderRegistry`: añadir un broker no toca el núcleo).
- Normalizadores por exchange → modelo interno único, tipado e inmutable
  (Ticker/Trade/OHLCV/Candle/OrderBook/DepthLevel/Funding/OpenInterest/
  Liquidation/MarketSnapshot/MarketState) con doble timestamp UTC + latencia.
- WebSocket manager: reconexión con backoff exponencial + jitter, heartbeat
  ping/pong, compresión, detección de conexión muda, resuscripción automática,
  métricas y latencia por conexión; transporte inyectable (tests sin red).
- Pipeline `TickCollector`: validación de calidad (duplicados, fuera de orden,
  gaps imposibles, timestamps inválidos, volumen absurdo) → estado vivo →
  agregador de velas (cualquier timeframe: tick a mensual, VWAP, volumen
  comprador/vendedor) → order book incremental con resync REST → cache Redis
  degradable → persistencia batched con spill a disco → eventos.
- Primeras tablas: `market_ticks` y `market_candles` (migración Alembic 0001).
- `MarketDataService`: API interna agnóstica del exchange (get_last_price,
  get_orderbook, get_latest_candle, get_tick_stream, get_market_snapshot,
  get_spread, get_depth, get_recent_trades, get_open_interest,
  get_funding_rate, subscribe/unsubscribe).
- Endpoints `/api/market/*`, jobs del scheduler (flush de velas, vigilancia de
  conexiones, drift de reloj, métricas) y eventos nuevos del bus.
- 136 tests (94 nuevos); ruff + black + mypy strict en verde.
- Smoke test real: stream público de Binance con BTCUSDT/ETHUSDT en vivo
  (~500 msg/s, ~3.200 ticks en 20 s) servido por la API.

**Decisiones técnicas:** ADR-013…ADR-019 en `docs/architecture.md`
(incluye riesgos conocidos y próximas tareas).

**Pendiente para Fase 3:** Quant Core + Strategy Engine (consenso
multi-estrategia), backfill histórico, MT5/OANDA para XAUUSD real,
reconciliación automática de spills.
"""

_ENTRIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "2": (
        "Fase 2 completada — Data Engine",
        _FASE_2,
        ("fase-2", "data-engine", "mercado"),
    ),
}


async def main() -> None:
    """Append the requested phase milestone to the journal."""
    phase = sys.argv[1] if len(sys.argv) > 1 else max(_ENTRIES)
    title, content, tags = _ENTRIES[phase]
    service = DocumentationService()
    service.register_backend(MarkdownJournalBackend(_ROOT / "docs" / "bitacora.md"))
    await service.record_milestone(title, content, tags=tags)
    print(f"Bitácora actualizada: docs/bitacora.md ({title})")


if __name__ == "__main__":
    asyncio.run(main())
