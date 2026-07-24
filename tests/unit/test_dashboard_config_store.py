"""Config Center: los cambios del dashboard se aplican al motor en vivo.

Antes el store solo persistía overrides en disco sin tocar el settings vivo, así
que la pantalla de settings era decorativa. Ahora ``apply`` muta el objeto de
settings que los subsistemas leen en cada evaluación.
"""

from app.config.settings import Settings
from app.dashboard.api.config_store import RuntimeConfigStore, _is_live
from app.execution.risk_manager import RiskManager


def test_apply_mutates_live_settings():
    s = Settings()
    store = RuntimeConfigStore(None)
    assert s.execution.risk.max_consecutive_losses == 5
    applied = store.apply(s, {"execution.risk.max_consecutive_losses": 0})
    assert applied == {"execution.risk.max_consecutive_losses": 0}
    assert s.execution.risk.max_consecutive_losses == 0


def test_risk_manager_sees_live_change():
    """El RiskManager comparte el objeto: el cambio le llega sin reconstruir."""
    s = Settings()
    store = RuntimeConfigStore(None)
    rm = RiskManager(s.execution.risk, 1000.0)
    store.apply(s, {"execution.risk.max_open_positions": 9})
    assert rm._settings.max_open_positions == 9


def test_coercion_respects_types():
    s = Settings()
    store = RuntimeConfigStore(None)
    # Enviado como string desde el form → coaccionado al tipo del setting.
    store.apply(s, {"execution.risk.max_open_positions": "7"})
    assert s.execution.risk.max_open_positions == 7
    assert isinstance(s.execution.risk.max_open_positions, int)


def test_reapply_pushes_persisted_overrides(tmp_path):
    path = tmp_path / "runtime_config.json"
    first = RuntimeConfigStore(path)
    first.apply(Settings(), {"execution.risk.max_consecutive_losses": 12})

    # Simular reinicio: settings nuevo + store que recarga del disco.
    fresh = Settings()
    reloaded = RuntimeConfigStore(path)
    assert fresh.execution.risk.max_consecutive_losses == 5  # aún el default
    reloaded.reapply(fresh)
    assert fresh.execution.risk.max_consecutive_losses == 12


def test_unknown_key_is_rejected():
    s = Settings()
    store = RuntimeConfigStore(None)
    try:
        store.apply(s, {"execution.risk.not_a_field": 1})
        raise AssertionError("debería rechazar clave fuera del whitelist")
    except KeyError:
        pass


def test_live_flag_classifies_paths():
    assert _is_live("execution.risk.max_consecutive_losses") is True
    assert _is_live("quant.context.max_spread_bps") is True
    assert _is_live("paper.initial_balance") is False
