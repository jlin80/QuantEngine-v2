"""Carga dinámica de plugins de estrategia."""

from pathlib import Path

import pytest
from app.core.exceptions import ConfigurationError
from app.engine.plugins import PluginLoader

VALID_PLUGIN = """
from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.models import Cadence, CadenceKind, Direction, StrategySignal
from app.market.models import Timeframe


class DemoStrategy(BaseStrategy):
    name = "demo"
    version = "1.1"
    symbols = ("BTCUSDT",)
    timeframe = Timeframe.M1
    cadence = Cadence(kind=CadenceKind.EVERY_CANDLE, timeframe=Timeframe.M1)

    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            timestamp=ctx.fired_at,
            direction=Direction.LONG,
            confidence=0.8,
            score=80.0,
            reasons=("demo",),
        )
"""

BROKEN_PLUGIN = "this is not valid python ((("

DUPLICATE_PLUGIN = VALID_PLUGIN.replace("class DemoStrategy", "class OtherStrategy")


def test_discovers_strategy_from_directory(tmp_path: Path):
    (tmp_path / "demo.py").write_text(VALID_PLUGIN, encoding="utf-8")
    loader = PluginLoader([tmp_path])

    classes = loader.discover()

    assert len(classes) == 1
    assert classes[0].name == "demo"
    assert classes[0].version == "1.1"
    instance = classes[0]({"custom": 1})
    assert instance.parameters == {"custom": 1}


def test_broken_plugin_never_blocks_the_rest(tmp_path: Path):
    (tmp_path / "a_broken.py").write_text(BROKEN_PLUGIN, encoding="utf-8")
    (tmp_path / "b_good.py").write_text(VALID_PLUGIN, encoding="utf-8")

    classes = PluginLoader([tmp_path]).discover()

    assert [cls.name for cls in classes] == ["demo"]


def test_private_files_are_ignored(tmp_path: Path):
    (tmp_path / "_hidden.py").write_text(VALID_PLUGIN, encoding="utf-8")
    assert PluginLoader([tmp_path]).discover() == []


def test_duplicate_strategy_name_keeps_first(tmp_path: Path):
    (tmp_path / "a_first.py").write_text(VALID_PLUGIN, encoding="utf-8")
    (tmp_path / "b_second.py").write_text(DUPLICATE_PLUGIN, encoding="utf-8")

    classes = PluginLoader([tmp_path]).discover()

    assert len(classes) == 1
    assert classes[0].__name__ == "DemoStrategy"


def test_missing_directory_is_tolerated(tmp_path: Path):
    assert PluginLoader([tmp_path / "nope"]).discover() == []


def test_load_class_specific(tmp_path: Path):
    path = tmp_path / "demo.py"
    path.write_text(VALID_PLUGIN, encoding="utf-8")
    loader = PluginLoader([tmp_path])

    cls = loader.load_class(path, "DemoStrategy")
    assert cls.name == "demo"

    with pytest.raises(ConfigurationError):
        loader.load_class(path, "GhostStrategy")
