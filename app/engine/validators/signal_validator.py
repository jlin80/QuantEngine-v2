"""Validación estructural de señales antes de entrar al Signal Engine."""

from datetime import datetime

from app.engine.models import Direction, StrategySignal


class SignalValidator:
    """Structural gate for every emitted signal (independiente del emisor)."""

    def validate(self, signal: StrategySignal, *, now: datetime) -> list[str]:
        """Check a signal's internal coherence; empty list = valid.

        Args:
            signal: Señal a validar.
            now: Reloj actual (UTC).

        Returns:
            Problemas encontrados (cualquiera implica rechazo).
        """
        problems: list[str] = []
        if not signal.symbol:
            problems.append("símbolo vacío")
        if not signal.strategy_name or signal.strategy_name == "unnamed":
            problems.append("estrategia sin nombre")
        if not 0.0 <= signal.confidence <= 1.0:
            problems.append(f"confidence fuera de [0,1]: {signal.confidence}")
        if not 0.0 <= signal.score <= 100.0:
            problems.append(f"score fuera de [0,100]: {signal.score}")
        if not signal.reasons:
            problems.append("sin razones (explicabilidad obligatoria)")
        if signal.expiration is not None and signal.expiration <= now:
            problems.append("expiración en el pasado")
        problems.extend(self._level_checks(signal))
        return problems

    @staticmethod
    def _level_checks(signal: StrategySignal) -> list[str]:
        """SL/TP/entry coherence for directional signals."""
        problems: list[str] = []
        zone = signal.entry_zone
        if zone is not None and zone.low > zone.high:
            problems.append(f"entry_zone invertida: [{zone.low}, {zone.high}]")
        if signal.direction is Direction.NEUTRAL:
            return problems
        reference = None if zone is None else (zone.low + zone.high) / 2.0
        if reference is not None and signal.stop_loss is not None:
            if signal.direction is Direction.LONG and signal.stop_loss >= reference:
                problems.append("stop_loss por encima de la entrada en un long")
            if signal.direction is Direction.SHORT and signal.stop_loss <= reference:
                problems.append("stop_loss por debajo de la entrada en un short")
        if reference is not None and signal.take_profit is not None:
            if signal.direction is Direction.LONG and signal.take_profit <= reference:
                problems.append("take_profit por debajo de la entrada en un long")
            if signal.direction is Direction.SHORT and signal.take_profit >= reference:
                problems.append("take_profit por encima de la entrada en un short")
        if signal.risk_reward is not None and signal.risk_reward <= 0:
            problems.append(f"risk_reward no positivo: {signal.risk_reward}")
        return problems
