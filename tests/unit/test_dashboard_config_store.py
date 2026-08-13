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


# --------------------------------------------------------------------------
# Validación de tipos. Los settings anidados no llevan ``validate_assignment``,
# así que sin esta puerta el Config Center escribía cualquier cosa sobre el
# motor vivo.
# --------------------------------------------------------------------------


def test_dict_field_accepts_mapping_and_json_text():
    """Un campo `dict` se puede editar como objeto o como JSON en texto."""
    s = Settings()
    store = RuntimeConfigStore(None)
    store.apply(s, {"execution.symbols_enabled": {"XAUUSDM": True}})
    assert s.execution.symbols_enabled == {"XAUUSDM": True}
    # El textarea del formulario manda una cadena.
    store.apply(s, {"execution.symbols_enabled": '{"XAUUSDM": false}'})
    assert s.execution.symbols_enabled == {"XAUUSDM": False}


def test_dict_field_rejects_stringified_object():
    """El caso real: ``String(value)`` en el front producía ``[object Object]``.

    Se guardaba tal cual sobre un `dict[str, bool]` y el siguiente tick moría
    con ``AttributeError`` al llamar ``.get()`` sobre un `str`, dentro del
    camino caliente de ejecución.
    """
    s = Settings()
    store = RuntimeConfigStore(None)
    try:
        store.apply(s, {"execution.symbols_enabled": "[object Object]"})
        raise AssertionError("debería rechazar un dict serializado a la fuerza")
    except ValueError as exc:
        assert "JSON" in str(exc)
    assert s.execution.symbols_enabled == {}


def test_dict_field_rejects_wrong_value_type():
    s = Settings()
    store = RuntimeConfigStore(None)
    try:
        store.apply(s, {"execution.symbols_enabled": {"XAUUSDM": "quizá"}})
        raise AssertionError("debería rechazar un valor no booleano")
    except ValueError:
        pass
    assert s.execution.symbols_enabled == {}


def test_number_field_rejects_non_numeric_text():
    s = Settings()
    store = RuntimeConfigStore(None)
    before = s.execution.risk.max_open_positions
    try:
        store.apply(s, {"execution.risk.max_open_positions": "abc"})
        raise AssertionError("debería rechazar texto en un campo numérico")
    except ValueError:
        pass
    assert s.execution.risk.max_open_positions == before


def test_patch_is_all_or_nothing():
    """Un valor malo no debe dejar aplicada la mitad buena del parche."""
    s = Settings()
    store = RuntimeConfigStore(None)
    before = s.execution.risk.max_open_positions
    try:
        store.apply(
            s,
            {
                "execution.risk.max_open_positions": 9,
                "execution.symbols_enabled": "[object Object]",
            },
        )
        raise AssertionError("debería rechazar el parche entero")
    except ValueError:
        pass
    assert s.execution.risk.max_open_positions == before
    assert store.effective(s)["execution.risk.max_open_positions"]["overridden"] is False


def test_effective_publishes_kind_and_live():
    """El front elige el control por el esquema, no por el typeof del valor."""
    s = Settings()
    effective = RuntimeConfigStore(None).effective(s)
    assert effective["execution.symbols_enabled"]["kind"] == "dict"
    assert effective["execution.trailing_enabled"]["kind"] == "bool"
    assert effective["execution.risk.max_open_positions"]["kind"] == "number"
    assert effective["discord.min_level"]["kind"] == "string"
    assert effective["execution.symbols_enabled"]["live"] is True
    assert effective["execution.trailing_enabled"]["live"] is False


def test_dead_risk_paths_are_gone():
    """``RiskSettings`` raíz no lo lee nadie: no puede ser editable.

    Eran tres campos con nombres casi idénticos a los ``execution.risk.*`` que
    sí mandan. Bajar ahí el drawdown máximo no cambiaba nada y parecía que sí.
    """
    from app.dashboard.api.config_store import WHITELIST

    assert not [path for path in WHITELIST if path.startswith("risk.")]
    # El freno diario real sí es editable.
    assert "quant.filters.max_drawdown_pct" in WHITELIST


def test_daily_drawdown_filter_path_resolves():
    """El path publicado es el que lee el DrawdownFilter, no un homónimo."""
    s = Settings()
    store = RuntimeConfigStore(None)
    store.apply(s, {"quant.filters.max_drawdown_pct": 7.5})
    assert s.quant.filters.max_drawdown_pct == 7.5
