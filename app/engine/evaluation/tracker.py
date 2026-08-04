"""Framework de Evaluación Continua (Fase 4).

Cada señal con niveles (entrada + stop) abre una "operación virtual" que se
resuelve contra las velas posteriores: TP, SL o timeout. De ahí salen las
estadísticas por estrategia (win rate, profit factor, expectativa en R,
drawdown, falsas señales, tiempo medio en operación).

Estas métricas NO se usan para operar todavía: se almacenan (memoria +
snapshot JSON) para que fases posteriores ajusten pesos dinámicamente sin
modificar el código de ninguna estrategia (``factor()`` ya expone el hook).
"""

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config.settings import QuantEvaluationSettings
from app.core.lifecycle import Service
from app.engine.models import Direction, SignalRecord
from app.market.models import Timeframe
from app.market.services import MarketDataService
from app.utils.time import utc_now


@dataclass(kw_only=True, slots=True)
class _VirtualTrade:
    """Posición hipotética abierta por una señal (nunca se envía a broker)."""

    signal_id: str
    strategy: str
    symbol: str
    direction: Direction
    entry: float
    stop: float
    target: float | None
    risk: float
    opened_at: datetime
    deadline: datetime


@dataclass(kw_only=True, slots=True)
class StrategyPerformance:
    """Estadísticas acumuladas de una estrategia (resultados virtuales)."""

    strategy: str
    signals: int = 0
    tracked: int = 0
    evaluated: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    false_signals: int = 0
    gross_win_r: float = 0.0
    gross_loss_r: float = 0.0
    holding_seconds_total: float = 0.0
    last_result_r: float | None = None
    r_values: deque[float] = field(default_factory=lambda: deque(maxlen=500))

    @property
    def win_rate(self) -> float | None:
        """Fracción de resoluciones ganadoras (None sin muestra)."""
        decided = self.wins + self.losses
        return self.wins / decided if decided > 0 else None

    @property
    def profit_factor(self) -> float | None:
        """Ganancia bruta / pérdida bruta en R (None sin pérdidas)."""
        if self.gross_loss_r <= 0:
            return None
        return self.gross_win_r / self.gross_loss_r

    @property
    def expectancy_r(self) -> float | None:
        """Resultado medio por operación, en R."""
        if not self.r_values:
            return None
        return sum(self.r_values) / len(self.r_values)

    @property
    def max_drawdown_r(self) -> float:
        """Máximo drawdown de la curva acumulada de R."""
        peak = 0.0
        equity = 0.0
        drawdown = 0.0
        for r in self.r_values:
            equity += r
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return drawdown

    @property
    def avg_holding_seconds(self) -> float | None:
        """Tiempo medio en operación (segundos)."""
        return self.holding_seconds_total / self.evaluated if self.evaluated > 0 else None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "strategy": self.strategy,
            "signals": self.signals,
            "tracked": self.tracked,
            "evaluated": self.evaluated,
            "wins": self.wins,
            "losses": self.losses,
            "timeouts": self.timeouts,
            "false_signals": self.false_signals,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expectancy_r": self.expectancy_r,
            "max_drawdown_r": round(self.max_drawdown_r, 4),
            "avg_holding_seconds": self.avg_holding_seconds,
            "last_result_r": self.last_result_r,
        }


class PerformanceTracker(Service):
    """Evaluates every signal's virtual outcome and keeps per-strategy stats.

    Args:
        settings: Configuración de la evaluación continua.
        market: API de datos (velas para resolver TP/SL).
    """

    def __init__(self, settings: QuantEvaluationSettings, market: MarketDataService) -> None:
        super().__init__("performance_tracker")
        self._settings = settings
        self._market = market
        self._timeframe = Timeframe(settings.timeframe)
        self._open: dict[str, _VirtualTrade] = {}
        self._stats: dict[str, StrategyPerformance] = {}
        self._seen: deque[str] = deque(maxlen=20_000)
        self._seen_set: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._last_snapshot = 0.0
        self._log = logging.getLogger("app.engine.performance")

    # ------------------------------------------------------------------
    # Entrada (sink del historial de señales)
    # ------------------------------------------------------------------

    def on_signal_record(self, record: SignalRecord) -> None:
        """Track a signal's virtual trade (idempotente por signal_id)."""
        if not self._settings.enabled:
            return
        signal = record.signal
        if signal.signal_id in self._seen_set:
            return
        if len(self._seen) == self._seen.maxlen and self._seen.maxlen is not None:
            self._seen_set.discard(self._seen[0])
        self._seen.append(signal.signal_id)
        self._seen_set.add(signal.signal_id)

        perf = self._perf(signal.strategy_name)
        perf.signals += 1
        if signal.direction is Direction.NEUTRAL or signal.stop_loss is None:
            return
        if signal.entry_zone is not None:
            entry = (signal.entry_zone.low + signal.entry_zone.high) / 2.0
        else:
            return  # sin entrada propuesta no hay operación virtual medible
        risk = abs(entry - signal.stop_loss)
        if risk <= 0:
            return
        perf.tracked += 1
        self._open[signal.signal_id] = _VirtualTrade(
            signal_id=signal.signal_id,
            strategy=signal.strategy_name,
            symbol=signal.symbol,
            direction=signal.direction,
            entry=entry,
            stop=signal.stop_loss,
            target=signal.take_profit,
            risk=risk,
            opened_at=signal.timestamp,
            deadline=signal.timestamp + timedelta(minutes=self._settings.max_holding_minutes),
        )

    # ------------------------------------------------------------------
    # Resolución
    # ------------------------------------------------------------------

    def evaluate_open(self, now: datetime | None = None) -> int:
        """Resolve open virtual trades against the candles seen since entry.

        Regla conservadora: si SL y TP caen en la misma vela, cuenta SL.

        Returns:
            Cuántas operaciones virtuales se resolvieron en esta pasada.
        """
        moment = now or utc_now()
        resolved = 0
        for trade in list(self._open.values()):
            # Sólo velas que EMPIEZAN después de la entrada. Con el filtro
            # anterior (`c.end > opened_at`) entraba la vela EN CURSO en el
            # momento de la señal, cuyo rango high/low incluye precio previo a
            # la señal: la operación virtual se resolvía contra movimiento que
            # ya había ocurrido. Efecto medido: `choch` daba una duración media
            # de ~4 s (la señal disparaba cerca del cierre de vela y esa misma
            # vela la resolvía) y una expectativa inflada. No es un caso
            # particular de `choch`: contaminaba a toda estrategia que dispara
            # tarde dentro de la vela. Ahora la resolución sólo usa precio
            # posterior a la entrada, que es lo único que la señal pudo prever.
            candles = [
                c
                for c in self._market.get_candles(trade.symbol, self._timeframe, 500)
                if c.start >= trade.opened_at
            ]
            done = False
            for i, candle in enumerate(candles):
                long = trade.direction is Direction.LONG
                hit_stop = candle.low <= trade.stop if long else candle.high >= trade.stop
                hit_target = trade.target is not None and (
                    candle.high >= trade.target if long else candle.low <= trade.target
                )
                if hit_stop:
                    self._resolve(
                        trade,
                        r_value=-1.0,
                        outcome="loss",
                        closed_at=candle.end,
                        false_signal=i < self._settings.false_signal_bars,
                    )
                    done = True
                    break
                if hit_target and trade.target is not None:
                    reward = abs(trade.target - trade.entry) / trade.risk
                    self._resolve(
                        trade,
                        r_value=reward,
                        outcome="win",
                        closed_at=candle.end,
                        false_signal=False,
                    )
                    done = True
                    break
            if done:
                resolved += 1
                continue
            if moment >= trade.deadline:
                last_price = candles[-1].close if candles else trade.entry
                signed = last_price - trade.entry
                if trade.direction is Direction.SHORT:
                    signed = -signed
                self._resolve(
                    trade,
                    r_value=signed / trade.risk,
                    outcome="timeout",
                    closed_at=moment,
                    false_signal=False,
                )
                resolved += 1
        return resolved

    def _resolve(
        self,
        trade: _VirtualTrade,
        *,
        r_value: float,
        outcome: str,
        closed_at: datetime,
        false_signal: bool,
    ) -> None:
        """Close a virtual trade and update its strategy's stats."""
        self._open.pop(trade.signal_id, None)
        perf = self._perf(trade.strategy)
        perf.evaluated += 1
        if outcome == "win":
            perf.wins += 1
        elif outcome == "loss":
            perf.losses += 1
        else:
            perf.timeouts += 1
        if false_signal:
            perf.false_signals += 1
        if r_value >= 0:
            perf.gross_win_r += r_value
        else:
            perf.gross_loss_r += -r_value
        perf.r_values.append(r_value)
        perf.last_result_r = round(r_value, 4)
        perf.holding_seconds_total += max(0.0, (closed_at - trade.opened_at).total_seconds())

    def _perf(self, strategy: str) -> StrategyPerformance:
        """Stats holder for a strategy (creado on-demand)."""
        perf = self._stats.get(strategy)
        if perf is None:
            perf = StrategyPerformance(
                strategy=strategy,
                r_values=deque(maxlen=self._settings.r_history_limit),
            )
            self._stats[strategy] = perf
        return perf

    # ------------------------------------------------------------------
    # Salida (hook para fases futuras + dashboard)
    # ------------------------------------------------------------------

    def factor(self, strategy: str) -> float:
        """Relevancia 0-1 según expectativa reciente (0.5 = neutro).

        Preparado para que el Decision Engine module pesos en fases futuras;
        exige una muestra mínima antes de apartarse del neutro.
        """
        perf = self._stats.get(strategy)
        if perf is None or perf.evaluated < 10:
            return 0.5
        expectancy = perf.expectancy_r or 0.0
        return max(0.0, min(1.0, 0.5 + max(-1.0, min(1.0, expectancy)) / 2.0))

    def performance(self, strategy: str) -> StrategyPerformance | None:
        """Stats of one strategy (None si nunca emitió señales)."""
        return self._stats.get(strategy)

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "open_virtual_trades": len(self._open),
            "strategies": {name: perf.to_dict() for name, perf in sorted(self._stats.items())},
        }

    def snapshot_to_disk(self) -> None:
        """Persist current stats as JSON (best effort, nunca lanza)."""
        try:
            path = Path(self._settings.snapshot_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"generated_at": utc_now().isoformat(), **self.status()}
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except OSError:
            self._log.exception("Performance snapshot failed")

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Evaluate periodically and snapshot on its own cadence."""
        while True:
            await asyncio.sleep(self._settings.check_interval_seconds)
            try:
                self.evaluate_open()
                now = time.monotonic()
                if now - self._last_snapshot >= self._settings.snapshot_interval_seconds:
                    self._last_snapshot = now
                    self.snapshot_to_disk()
            except Exception:  # el evaluador jamás debe morir
                self._log.exception("Continuous evaluation pass failed")

    async def _on_start(self) -> None:
        if self._settings.enabled:
            self._task = asyncio.create_task(self._loop(), name="performance-tracker")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._settings.enabled:
            self.snapshot_to_disk()
