"""Dataset Manager: carga y prepara series históricas para el laboratorio.

Convierte CSV / JSON Lines de OHLCV en velas internas (:class:`Candle`),
deduplicando por instante de inicio y garantizando orden cronológico. También
genera series sintéticas deterministas (semilla fija) para pruebas y para los
benchmarks. No duplica datos: la misma vela nunca aparece dos veces.
"""

import csv
import json
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.market.models import Candle, Timeframe

_TIME_KEYS = ("start", "timestamp", "time", "date", "open_time", "t")
_OPEN_KEYS = ("open", "o")
_HIGH_KEYS = ("high", "h")
_LOW_KEYS = ("low", "l")
_CLOSE_KEYS = ("close", "c")
_VOLUME_KEYS = ("volume", "vol", "v")


class DatasetError(ValueError):
    """A dataset could not be parsed or was empty."""


def _first(row: Mapping[str, object], keys: Sequence[str]) -> object | None:
    """Return the first present value among ``keys`` (case-insensitive)."""
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key in lowered and lowered[key] not in ("", None):
            return lowered[key]
    return None


def _parse_time(value: object) -> datetime:
    """Parse an ISO string or epoch number into a UTC datetime.

    Args:
        value: ISO-8601 string, or epoch in seconds/millis/micros.

    Returns:
        Timezone-aware UTC datetime.

    Raises:
        DatasetError: If the value cannot be parsed as a timestamp.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float) and not isinstance(value, bool):
        epoch = float(value)
        # Normaliza nanos/micros/millis a segundos según la magnitud.
        if epoch >= 1e16:
            epoch /= 1e9
        elif epoch >= 1e13:
            epoch /= 1e6
        elif epoch >= 1e10:
            epoch /= 1e3
        return datetime.fromtimestamp(epoch, tz=UTC)
    text = str(value).strip()
    if text.isdigit():
        return _parse_time(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DatasetError(f"Timestamp no reconocido: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _num(value: object, field: str) -> float:
    """Parse a numeric field, raising :class:`DatasetError` on failure."""
    try:
        return float(str(value))
    except (TypeError, ValueError) as exc:
        raise DatasetError(f"Campo numérico inválido ({field}): {value!r}") from exc


class DatasetManager:
    """Load and prepare OHLCV datasets as internal candle series.

    Args:
        provider: Etiqueta de proveedor asignada a las velas cargadas.
    """

    def __init__(self, *, provider: str = "backtest") -> None:
        self._provider = provider

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------

    def load(self, path: Path | str, symbol: str, timeframe: str | Timeframe) -> list[Candle]:
        """Load a dataset file (``.csv`` or ``.jsonl``) into candles.

        Args:
            path: Ruta al archivo de datos.
            symbol: Símbolo interno de las velas.
            timeframe: Timeframe de la serie.

        Returns:
            Velas cerradas, deduplicadas y en orden cronológico.

        Raises:
            DatasetError: Si el archivo no existe, está vacío o es ilegible.
        """
        file = Path(path)
        if not file.exists():
            raise DatasetError(f"Dataset inexistente: {file}")
        rows = self._read_jsonl(file) if file.suffix == ".jsonl" else self._read_csv(file)
        return self.from_rows(rows, symbol, timeframe)

    @staticmethod
    def _read_csv(file: Path) -> list[dict[str, object]]:
        """Read a CSV file into a list of row dicts."""
        with file.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    @staticmethod
    def _read_jsonl(file: Path) -> list[dict[str, object]]:
        """Read a JSON-Lines file into a list of row dicts."""
        rows: list[dict[str, object]] = []
        for line in file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def from_rows(
        self, rows: Iterable[Mapping[str, object]], symbol: str, timeframe: str | Timeframe
    ) -> list[Candle]:
        """Build candles from raw OHLCV row mappings.

        Args:
            rows: Filas con claves de tiempo y OHLCV (nombres flexibles).
            symbol: Símbolo interno.
            timeframe: Timeframe de la serie.

        Returns:
            Velas cerradas, deduplicadas por inicio y ordenadas.

        Raises:
            DatasetError: Si no hay ninguna fila válida.
        """
        tf = Timeframe(timeframe) if not isinstance(timeframe, Timeframe) else timeframe
        sym = symbol.upper()
        by_start: dict[datetime, Candle] = {}
        for row in rows:
            candle = self._row_to_candle(row, sym, tf)
            by_start[candle.start] = candle  # dedupe: la última gana
        if not by_start:
            raise DatasetError("El dataset no contiene ninguna vela válida")
        return [by_start[start] for start in sorted(by_start)]

    def _row_to_candle(self, row: Mapping[str, object], symbol: str, tf: Timeframe) -> Candle:
        """Convert one OHLCV row into a closed candle."""
        raw_time = _first(row, _TIME_KEYS)
        if raw_time is None:
            raise DatasetError(f"Fila sin timestamp: {dict(row)!r}")
        start = _parse_time(raw_time)
        open_ = _num(_first(row, _OPEN_KEYS), "open")
        high = _num(_first(row, _HIGH_KEYS), "high")
        low = _num(_first(row, _LOW_KEYS), "low")
        close = _num(_first(row, _CLOSE_KEYS), "close")
        volume_raw = _first(row, _VOLUME_KEYS)
        volume = _num(volume_raw, "volume") if volume_raw is not None else 0.0
        return Candle(
            symbol=symbol,
            provider=self._provider,
            timeframe=tf,
            start=start,
            end=tf.bucket_end(start),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            closed=True,
            source="backtest",
        )

    # ------------------------------------------------------------------
    # Series sintéticas (pruebas y benchmarks)
    # ------------------------------------------------------------------

    def synthetic(
        self,
        symbol: str,
        timeframe: str | Timeframe,
        *,
        count: int,
        start: datetime | None = None,
        initial_price: float = 100.0,
        drift: float = 0.0,
        volatility: float = 0.01,
        seed: int = 7,
    ) -> list[Candle]:
        """Generate a deterministic random-walk candle series.

        Args:
            symbol: Símbolo interno.
            timeframe: Timeframe de la serie.
            count: Número de velas.
            start: Inicio de la primera vela (por defecto un instante fijo UTC).
            initial_price: Precio de apertura de la primera vela.
            drift: Deriva por vela (fracción del precio) — tendencia.
            volatility: Amplitud relativa del ruido por vela.
            seed: Semilla del RNG (reproducibilidad).

        Returns:
            Velas cerradas coherentes (high ≥ max(open, close), etc.).
        """
        tf = Timeframe(timeframe) if not isinstance(timeframe, Timeframe) else timeframe
        rng = random.Random(seed)
        moment = start or datetime(2024, 1, 1, tzinfo=UTC)
        price = initial_price
        candles: list[Candle] = []
        for _ in range(count):
            open_ = price
            shock = rng.gauss(0.0, 1.0) * volatility
            close = max(1e-6, open_ * (1.0 + drift + shock))
            spread = abs(open_ * volatility) + abs(close - open_)
            high = max(open_, close) + abs(rng.gauss(0.0, 0.5)) * spread
            low = max(1e-9, min(open_, close) - abs(rng.gauss(0.0, 0.5)) * spread)
            volume = 100.0 + abs(rng.gauss(0.0, 1.0)) * 50.0
            candles.append(
                Candle(
                    symbol=symbol.upper(),
                    provider=self._provider,
                    timeframe=tf,
                    start=moment,
                    end=tf.bucket_end(moment),
                    open=round(open_, 6),
                    high=round(high, 6),
                    low=round(low, 6),
                    close=round(close, 6),
                    volume=round(volume, 4),
                    closed=True,
                    source="synthetic",
                )
            )
            price = close
            moment = tf.bucket_end(moment)
        return candles

    @staticmethod
    def dedupe(candles: Sequence[Candle]) -> list[Candle]:
        """Return candles with duplicate starts removed, chronologically.

        Args:
            candles: Velas posiblemente con solapes o desorden.

        Returns:
            Velas únicas por inicio, ordenadas de más antigua a más reciente.
        """
        by_start: dict[datetime, Candle] = {c.start: c for c in candles}
        return [by_start[start] for start in sorted(by_start)]


def sma(values: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` values.

    Args:
        values: Serie de valores.
        period: Ventana (número de valores).

    Returns:
        La media, o ``None`` si no hay suficientes valores.
    """
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return math.fsum(window) / period
