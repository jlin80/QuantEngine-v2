"""Conexión al terminal MetaTrader 5 (cuenta demo Exness).

Encapsula el ciclo de vida ``initialize`` → ``login`` → ``shutdown`` del paquete
``MetaTrader5`` y expone un healthcheck. El módulo ``MetaTrader5`` es un import
opcional (sólo existe en Windows con el terminal instalado): se resuelve de forma
perezosa y puede inyectarse para tests deterministas.

El adaptador nunca lanza en el ciclo de vida hacia afuera con detalles de la
librería: traduce fallos a :class:`~app.core.exceptions.BrokerConnectionError`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from types import ModuleType
from typing import Any, cast

_log = logging.getLogger("app.brokers.mt5.connection")


@dataclass(frozen=True, slots=True)
class MT5ConnectionConfig:
    """Parámetros de conexión a la cuenta demo.

    Attributes:
        login: Número de cuenta (login) demo de Exness.
        password: Contraseña de la cuenta (secreto — nunca se registra).
        server: Servidor del broker (p. ej. ``Exness-MT5Trial``).
        terminal_path: Ruta al ``terminal64.exe`` (``None`` = autodetección).
        timeout_ms: Timeout de ``initialize`` en milisegundos.
        portable: Si el terminal corre en modo portable.
    """

    login: int
    password: str
    server: str
    terminal_path: str | None = None
    timeout_ms: int = 60_000
    portable: bool = False


class BrokerConnectionError(RuntimeError):
    """La conexión con el broker MT5 falló o no está disponible."""


def _load_mt5() -> ModuleType:
    """Import perezoso del paquete ``MetaTrader5``.

    Returns:
        El módulo ``MetaTrader5``.

    Raises:
        BrokerConnectionError: Si el paquete no está instalado (p. ej. entorno de
            desarrollo no-Windows). El mensaje explica cómo instalarlo.
    """
    try:
        import MetaTrader5 as mt5  # noqa: N813  (nombre oficial del paquete)
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise BrokerConnectionError(
            "El paquete 'MetaTrader5' no está instalado. Es obligatorio para el "
            "modo demo y sólo funciona en Windows con el terminal MT5 instalado: "
            "instálalo con 'pip install MetaTrader5'."
        ) from exc
    return cast(ModuleType, mt5)


class MT5Connection:
    """Gestiona la sesión con el terminal MetaTrader 5.

    Thread-safe: ``MetaTrader5`` es un singleton global de proceso, así que todas
    las llamadas se serializan con un lock. El Execution Engine es asíncrono pero
    invoca al broker de forma síncrona; el lock protege contra el bucle de gestión
    y el feed de datos compartiendo el mismo terminal.

    Args:
        config: Parámetros de conexión demo.
        mt5: Módulo ``MetaTrader5`` inyectable (para tests). ``None`` = import real.
    """

    def __init__(self, config: MT5ConnectionConfig, mt5: ModuleType | None = None) -> None:
        self._config = config
        self._mt5 = mt5
        self._lock = threading.RLock()
        self._connected = False
        self._symbol_cache: dict[str, str] = {}

    @property
    def lock(self) -> threading.RLock:
        """Lock que serializa el acceso al terminal (compartido por broker+feed)."""
        return self._lock

    @property
    def mt5(self) -> ModuleType:
        """Módulo ``MetaTrader5`` (resuelto perezosamente en el primer uso)."""
        if self._mt5 is None:
            self._mt5 = _load_mt5()
        return self._mt5

    @property
    def connected(self) -> bool:
        """Whether the last known state is connected (no I/O)."""
        return self._connected

    def connect(self) -> None:
        """Inicializa el terminal e inicia sesión en la cuenta demo.

        Idempotente: si ya está conectada, no hace nada.

        Raises:
            BrokerConnectionError: Si ``initialize`` o ``login`` fallan.
        """
        with self._lock:
            if self._connected:
                return
            mt5 = self.mt5
            kwargs: dict[str, Any] = {
                "login": self._config.login,
                "password": self._config.password,
                "server": self._config.server,
                "timeout": self._config.timeout_ms,
                "portable": self._config.portable,
            }
            if self._config.terminal_path:
                ok = mt5.initialize(self._config.terminal_path, **kwargs)
            else:
                ok = mt5.initialize(**kwargs)
            if not ok:
                code, message = self._last_error()
                raise BrokerConnectionError(
                    f"MT5 initialize/login falló para la cuenta {self._config.login} "
                    f"en {self._config.server}: [{code}] {message}"
                )
            self._connected = True
            _log.info(
                "MT5 conectado: cuenta=%s servidor=%s", self._config.login, self._config.server
            )

    def disconnect(self) -> None:
        """Cierra la sesión con el terminal (nunca lanza)."""
        with self._lock:
            if self._mt5 is None:
                self._connected = False
                return
            try:
                self._mt5.shutdown()
            except Exception:  # pragma: no cover - shutdown best-effort
                _log.warning("MT5 shutdown falló", exc_info=True)
            finally:
                self._connected = False
                _log.info("MT5 desconectado")

    def healthcheck(self) -> bool:
        """Return whether the terminal responds and the account is reachable.

        Consulta ``account_info``: si devuelve ``None`` la sesión se perdió. No
        lanza — el Live Gate/Watchdog consumen el booleano.

        Returns:
            ``True`` si el terminal responde con información de cuenta.
        """
        with self._lock:
            if not self._connected or self._mt5 is None:
                return False
            try:
                info = self._mt5.account_info()
            except Exception:  # pragma: no cover - defensivo
                _log.warning("MT5 healthcheck lanzó", exc_info=True)
                return False
            if info is None:
                self._connected = False
                return False
            return True

    def account_info(self) -> Any:
        """Return the raw MT5 ``account_info`` named-tuple (or ``None``)."""
        with self._lock:
            return self.mt5.account_info()

    def resolve_symbol(self, symbol: str) -> str:
        """Map a system symbol to the terminal's real symbol name (case-insensitive).

        El resto del motor normaliza los símbolos a MAYÚSCULAS (``XAUUSDM``), pero
        los símbolos de MT5/Exness suelen llevar sufijos en minúscula
        (``XAUUSDm``). Esta resolución busca, sin distinguir mayúsculas, el nombre
        exacto que expone el terminal y lo cachea. Si no encuentra coincidencia,
        devuelve el símbolo tal cual (que fallará de forma visible más adelante).

        Args:
            symbol: Símbolo tal como lo usa el sistema (cualquier caja).

        Returns:
            El nombre real del símbolo en el terminal MT5.
        """
        key = symbol.lower()
        cached = self._symbol_cache.get(key)
        if cached is not None:
            return cached
        with self._lock:
            try:
                # Coincidencia exacta primero (evita escanear todo el catálogo).
                if self.mt5.symbol_info(symbol) is not None:
                    self._symbol_cache[key] = symbol
                    return symbol
                catalog = self.mt5.symbols_get() or ()
            except Exception:  # pragma: no cover - defensivo
                _log.warning("MT5 no pudo resolver el símbolo %s", symbol, exc_info=True)
                catalog = ()
            for info in catalog:
                name = getattr(info, "name", "")
                if name.lower() == key:
                    self._symbol_cache[key] = name
                    _log.info("Símbolo MT5 resuelto: %s -> %s", symbol, name)
                    return name
        self._symbol_cache[key] = symbol
        return symbol

    def account_balance(self) -> float | None:
        """Return the account balance, or ``None`` if the terminal is unreachable.

        Se usa para sembrar la contabilidad del motor con el balance REAL de la
        cuenta (demo) en vez de un número fijo del ``.env``.
        """
        try:
            info = self.account_info()
        except Exception:  # pragma: no cover - defensivo
            _log.warning("MT5 account_balance lanzó", exc_info=True)
            return None
        if info is None:
            return None
        balance = getattr(info, "balance", None)
        return float(balance) if balance is not None else None

    def _last_error(self) -> tuple[int, str]:
        """Return the terminal's ``last_error`` as ``(code, message)``."""
        try:
            err = self._mt5.last_error() if self._mt5 is not None else (-1, "sin módulo")
        except Exception:  # pragma: no cover - defensivo
            return (-1, "last_error no disponible")
        if isinstance(err, (tuple, list)) and len(err) >= 2:
            return int(err[0]), str(err[1])
        return (-1, str(err))
