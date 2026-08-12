"""Los overrides del Config Center deben llegar a los subsistemas.

`config_store.reapply()` corria DESPUES de construir los subsistemas, asi que
todo override no-hot se ignoraba para siempre: el dashboard decia "saved",
GET /api/config lo marcaba `overridden`, y el motor seguia con el valor del
.env porque el PositionManager ya habia copiado `trailing_enabled` a un
atributo propio. Fallo silencioso y facil de reintroducir.
"""

import pytest
from app.config.settings import Settings
from app.dashboard.api.config_store import config_store


@pytest.fixture(autouse=True)
def _clean_store(tmp_path):
    """Aisla el store en disco para no tocar los overrides reales."""
    original_path = config_store._path
    original = dict(config_store._overrides)
    config_store._path = tmp_path / "overrides.json"
    config_store._overrides = {}
    yield
    config_store._path = original_path
    config_store._overrides = original


def test_reapply_mutates_settings_before_use():
    settings = Settings()
    settings.execution.trailing_enabled = False

    config_store.apply(settings, {"execution.trailing_enabled": True})

    assert settings.execution.trailing_enabled is True


def test_overrides_survive_a_fresh_settings_object():
    """Es el caso del reinicio: settings nuevo desde .env + overrides guardados."""
    first = Settings()
    config_store.apply(first, {"execution.trailing_activate_r": 2.5})

    reloaded = Settings()
    assert reloaded.execution.trailing_activate_r == 1.0  # valor por defecto
    config_store.reapply(reloaded)

    assert reloaded.execution.trailing_activate_r == 2.5


def test_ignore_drawdown_limits_is_a_hot_toggle():
    """El toggle del dashboard debe aplicar sin reiniciar el motor."""
    from app.dashboard.api.config_store import _is_live

    settings = Settings()
    assert settings.execution.risk.ignore_drawdown_limits is False

    config_store.apply(settings, {"execution.risk.ignore_drawdown_limits": True})

    assert settings.execution.risk.ignore_drawdown_limits is True
    assert _is_live("execution.risk.ignore_drawdown_limits")


def test_reapply_runs_before_subsystems_are_built():
    """Ancla el ORDEN en bootstrap: reapply antes de construir nada.

    Si alguien vuelve a mover `reapply` despues de `_build_execution`, los
    overrides no-hot vuelven a ser decorativos sin que ningun test falle.
    """
    import inspect

    from app.engine import bootstrap

    source = inspect.getsource(bootstrap.build_container)
    reapply_at = source.index("config_store.reapply(settings)")
    for builder in ("_build_market(", "_build_quant(", "_build_execution(", "_build_ml("):
        assert reapply_at < source.index(builder), (
            f"config_store.reapply debe ir ANTES de {builder}: si no, los "
            "overrides no-hot no llegan al subsistema"
        )
