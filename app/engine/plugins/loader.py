"""Carga dinámica de estrategias (sistema de plugins).

Las estrategias viven como archivos .py en los directorios configurados
(p. ej. ``app/strategies/``). Añadir una estrategia = soltar un archivo con
una subclase de :class:`BaseStrategy`; el núcleo no se modifica jamás.
"""

import importlib.util
import inspect
import logging
import sys
from pathlib import Path

from app.core.exceptions import ConfigurationError
from app.engine.interfaces.strategy import BaseStrategy


class PluginLoader:
    """Discovers and instantiates strategy plugins from directories.

    Args:
        directories: Directorios a escanear (archivos ``*.py`` no privados).
    """

    def __init__(self, directories: list[Path]) -> None:
        self._directories = directories
        self._log = logging.getLogger("app.engine.plugins")

    def discover(self) -> list[type[BaseStrategy]]:
        """Scan every directory and return the strategy classes found.

        Los archivos que fallen al importar se saltan con un log de error —
        un plugin roto jamás impide cargar los demás.

        Returns:
            Clases concretas de estrategia (sin instanciar), por nombre.
        """
        classes: dict[str, type[BaseStrategy]] = {}
        for directory in self._directories:
            if not directory.is_dir():
                self._log.debug("Plugin dir %s does not exist — skipped", directory)
                continue
            for path in sorted(directory.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                for cls in self._load_file(path):
                    if cls.name in classes:
                        self._log.warning(
                            "Duplicate strategy name '%s' (%s) — keeping first",
                            cls.name,
                            path.name,
                        )
                        continue
                    classes[cls.name] = cls
        return list(classes.values())

    def _load_file(self, path: Path) -> list[type[BaseStrategy]]:
        """Import one file and extract its BaseStrategy subclasses."""
        module_name = f"qe_strategy_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return []
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception:
            self._log.exception("Failed to import strategy plugin %s", path)
            return []
        found: list[type[BaseStrategy]] = []
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseStrategy) and obj is not BaseStrategy:
                if obj.__module__ != module_name:
                    continue  # clases importadas dentro del plugin
                found.append(obj)
        if not found:
            self._log.debug("No strategies found in %s", path.name)
        return found

    def load_class(self, path: Path, class_name: str) -> type[BaseStrategy]:
        """Load one specific strategy class from a file.

        Args:
            path: Archivo del plugin.
            class_name: Nombre de la clase.

        Returns:
            La clase de estrategia.

        Raises:
            ConfigurationError: Si el archivo o la clase no existen.
        """
        for cls in self._load_file(path):
            if cls.__name__ == class_name:
                return cls
        raise ConfigurationError(
            f"Strategy class '{class_name}' not found in {path}",
            context={"path": str(path)},
        )
