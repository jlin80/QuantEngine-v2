"""Los umbrales de volatilidad tienen que clasificar algo.

Medido sobre 1187 operaciones reales de produccion, `volatility` llegaba
`normal` en el **100 %** de los casos: el umbral HIGH (0.80 % de ATR) estaba
calibrado para una escala temporal mucho mayor y en 1m es inalcanzable — el
maximo observado fue 0.261 %. Una variable constante no informa a nadie: ni al
`ConfidenceEngine`, ni a los filtros, ni al ML, que la recibia como feature
muerta.

Aqui se fija que los umbrales nuevos caen dentro del rango real, y que la
resolucion por simbolo funciona — porque la escala no es comparable entre
activos.
"""

import pytest
from app.config.settings import QuantContextSettings

# Distribucion real de ATR% en 1m, medida sobre el journal de `qevps`
# (2026-07-23 → 2026-08-04). (p5, mediana, p95, max)
REAL_ATR_PCT = {
    "BTCUSDM": (0.031, 0.054, 0.149, 0.201),
    "ETHUSDM": (0.035, 0.068, 0.165, 0.261),
    "USTECM": (0.033, 0.059, 0.171, 0.188),
    "XAUUSDM": (0.025, 0.038, 0.077, 0.116),
}


# ----------------------------------------------------------------------
# La regresion: el umbral inalcanzable
# ----------------------------------------------------------------------


@pytest.mark.parametrize("symbol", sorted(REAL_ATR_PCT))
def test_the_high_threshold_is_reachable_in_the_real_distribution(symbol):
    """El umbral viejo (0.80 %) era 3-30x el maximo observado: nunca disparaba."""
    _, _, _p95, maximum = REAL_ATR_PCT[symbol]
    high = QuantContextSettings().atr_pct_high_for(symbol)

    assert high < maximum, f"{symbol}: HIGH {high} inalcanzable (max real {maximum})"
    # Y no tan bajo que dispare siempre: debe quedar por encima de la mediana.
    assert high > REAL_ATR_PCT[symbol][1]


@pytest.mark.parametrize("symbol", sorted(REAL_ATR_PCT))
def test_the_low_threshold_actually_fires_sometimes(symbol):
    """Simetrico: por debajo del p5 no clasificaria nada como LOW."""
    p5, median, _, _ = REAL_ATR_PCT[symbol]
    low = QuantContextSettings().atr_pct_low_for(symbol)

    assert low > p5, f"{symbol}: LOW {low} por debajo del p5 real ({p5})"
    assert low < median


@pytest.mark.parametrize("symbol", sorted(REAL_ATR_PCT))
def test_the_normal_band_is_not_the_whole_distribution(symbol):
    """Si la banda cubre de p5 a max, `volatility` vuelve a ser constante.

    Es exactamente el fallo que este modulo corrige, asi que se fija.
    """
    settings = QuantContextSettings()
    p5, _, _, maximum = REAL_ATR_PCT[symbol]

    banda = (settings.atr_pct_low_for(symbol), settings.atr_pct_high_for(symbol))
    assert not (banda[0] <= p5 and banda[1] >= maximum)


# ----------------------------------------------------------------------
# Resolucion por simbolo
# ----------------------------------------------------------------------


def test_gold_and_eth_do_not_share_thresholds():
    """La escala no es comparable: oro mediana 0.038 %, ETH 0.068 %.

    Con un umbral unico, el oro seria LOW casi siempre y ETH casi nunca.
    """
    settings = QuantContextSettings()

    assert settings.atr_pct_low_for("XAUUSDM") < settings.atr_pct_low_for("ETHUSDM")
    assert settings.atr_pct_high_for("XAUUSDM") < settings.atr_pct_high_for("ETHUSDM")


def test_an_unknown_symbol_falls_back_to_the_global_threshold():
    """Un simbolo nuevo no puede quedarse sin clasificar."""
    settings = QuantContextSettings()

    assert settings.atr_pct_low_for("SOLUSDM") == settings.atr_pct_low
    assert settings.atr_pct_high_for("SOLUSDM") == settings.atr_pct_high


def test_the_lookup_is_case_insensitive():
    """El sistema usa mayusculas, pero el terminal expone `XAUUSDm`."""
    settings = QuantContextSettings()

    assert settings.atr_pct_low_for("xauusdm") == settings.atr_pct_low_for("XAUUSDM")


def test_low_is_always_below_high():
    """Invariante barata que evita una configuracion que no clasifica nada."""
    settings = QuantContextSettings()

    for symbol in [*REAL_ATR_PCT, "SIMBOLO_NUEVO"]:
        assert settings.atr_pct_low_for(symbol) < settings.atr_pct_high_for(symbol)
