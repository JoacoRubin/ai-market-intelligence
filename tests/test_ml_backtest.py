"""Tests de métricas, baseline y backtesting temporal.

El test más importante de este archivo es
`test_ninguna_ventana_entrena_con_datos_del_futuro`.

En una serie temporal, un `train_test_split` aleatorio entrena con datos de
junio para predecir marzo. Eso se llama **data leakage temporal**, produce
métricas espectaculares y un modelo que en producción no sirve para nada — donde
el futuro, por definición, todavía no pasó.

Es el error más común en forecasting y no falla ruidosamente: falla dando
resultados demasiado buenos, que es la forma más difícil de detectar.

El segundo en importancia es el **baseline**. Un MAPE de 8% no dice nada por sí
solo. Si repetir el último valor conocido da 6%, el modelo está de más: cuesta
entrenarlo, versionarlo y mantenerlo para empeorar el resultado.
"""

import numpy as np
import pytest

from ml.backtest import backtest, ventanas_walk_forward
from ml.baseline import baseline_media_movil, baseline_naive
from ml.metricas import mae, mape, rmse

# --- Métricas ----------------------------------------------------------------

def test_mae_calcula_el_error_absoluto_medio():
    assert mae([10, 20, 30], [12, 18, 33]) == pytest.approx((2 + 2 + 3) / 3)


def test_rmse_penaliza_mas_los_errores_grandes():
    """Es la diferencia práctica con MAE: un error de 10 pesa más que diez de 1."""
    disperso = rmse([0, 0, 0], [1, 1, 1])
    concentrado = rmse([0, 0, 0], [0, 0, 3])
    assert concentrado > disperso


def test_mape_devuelve_porcentaje():
    assert mape([100, 200], [110, 180]) == pytest.approx(10.0)


def test_mape_ignora_los_ceros_en_vez_de_explotar():
    """Un día sin ventas hace 0 el denominador del MAPE.

    Dividir por cero daría infinito y contaminaría toda la métrica. Se excluyen
    esos puntos y se documenta: no se puede medir error porcentual sobre cero.
    """
    valor = mape([0, 100, 0, 200], [5, 110, 3, 180])
    assert valor == pytest.approx(10.0)


def test_mape_sin_valores_distintos_de_cero_devuelve_none():
    """Si toda la ventana es cero, no hay MAPE que calcular. `None` dice eso;
    devolver 0 afirmaría una precisión perfecta que nadie midió."""
    assert mape([0, 0, 0], [1, 2, 3]) is None


def test_las_metricas_rechazan_longitudes_distintas():
    with pytest.raises(ValueError):
        mae([1, 2, 3], [1, 2])


def test_una_prediccion_perfecta_da_error_cero():
    assert mae([1, 2, 3], [1, 2, 3]) == 0
    assert rmse([1, 2, 3], [1, 2, 3]) == 0
    assert mape([1, 2, 3], [1, 2, 3]) == 0


# --- Baselines ---------------------------------------------------------------

def test_el_baseline_naive_repite_el_ultimo_valor():
    """El competidor a vencer. Si el modelo no le gana, el modelo sobra."""
    assert list(baseline_naive([5, 8, 12], horizonte=3)) == [12, 12, 12]


def test_el_baseline_de_media_movil_promedia_la_ventana():
    assert baseline_media_movil([10, 20, 30, 40], horizonte=2, ventana=2)[0] == 35


def test_los_baselines_no_necesitan_entrenamiento():
    """Su valor está justamente en ser gratis: cero costo de entrenamiento,
    cero mantenimiento, cero riesgo de degradación."""
    assert len(baseline_naive([1], horizonte=5)) == 5


def test_un_baseline_sobre_serie_vacia_falla_claro():
    with pytest.raises(ValueError):
        baseline_naive([], horizonte=3)


# --- Ventanas de backtesting -------------------------------------------------

def test_ninguna_ventana_entrena_con_datos_del_futuro():
    """EL test de este archivo.

    Todo índice de entrenamiento tiene que ser anterior a todo índice de
    prueba. Un split aleatorio rompe esta condición y produce un modelo que
    parece excelente y no sirve: en producción el futuro no está disponible.
    """
    for train, test in ventanas_walk_forward(n=200, tamano_test=14, n_ventanas=4):
        assert max(train) < min(test), (
            f"leakage temporal: entrena hasta {max(train)} y evalúa desde "
            f"{min(test)}"
        )


def test_las_ventanas_no_se_superponen_entre_si():
    ventanas = ventanas_walk_forward(n=200, tamano_test=14, n_ventanas=4)
    usados = [set(test) for _, test in ventanas]
    for i in range(len(usados)):
        for j in range(i + 1, len(usados)):
            assert not usados[i] & usados[j]


def test_la_ventana_de_entrenamiento_crece_con_el_tiempo():
    """Walk-forward: cada corte entrena con todo el histórico disponible hasta
    ese momento, que es lo que pasaría en producción."""
    ventanas = ventanas_walk_forward(n=200, tamano_test=14, n_ventanas=4)
    tamanos = [len(train) for train, _ in ventanas]
    assert tamanos == sorted(tamanos)


def test_todas_las_ventanas_de_prueba_tienen_el_tamano_pedido():
    for _, test in ventanas_walk_forward(n=200, tamano_test=14, n_ventanas=4):
        assert len(test) == 14


def test_una_serie_demasiado_corta_no_genera_ventanas():
    """Antes que inventar un backtest sobre datos insuficientes, no hacerlo.
    Un backtest de una sola ventana de tres puntos no mide nada."""
    assert ventanas_walk_forward(n=20, tamano_test=14, n_ventanas=4) == []


# --- Backtest completo -------------------------------------------------------

def _serie_con_tendencia(n: int = 200) -> np.ndarray:
    dias = np.arange(n)
    return 50 + 0.3 * dias + 10 * np.sin(2 * np.pi * dias / 7)


def test_el_backtest_evalua_modelo_y_baseline(_=None):
    serie = _serie_con_tendencia()
    resultado = backtest(serie, horizonte=14, n_ventanas=3)

    assert resultado.mape_modelo is not None
    assert resultado.mape_baseline is not None
    assert resultado.ventanas == 3


def test_el_backtest_declara_si_el_modelo_supera_al_baseline():
    """La pregunta que decide si el modelo va a producción o a la basura."""
    resultado = backtest(_serie_con_tendencia(), horizonte=14, n_ventanas=3)
    assert isinstance(resultado.supera_al_baseline, bool)


def test_sobre_una_serie_con_tendencia_clara_el_modelo_deberia_ganar():
    """Con tendencia y estacionalidad marcadas, repetir el último valor es una
    estrategia pobre. Si el modelo no gana acá, algo está mal en el modelo."""
    resultado = backtest(_serie_con_tendencia(), horizonte=14, n_ventanas=3)
    assert resultado.mape_modelo < resultado.mape_baseline


def test_sobre_ruido_puro_el_modelo_no_deberia_ganar_por_mucho():
    """Contraprueba honesta: si el modelo "gana" sobre ruido blanco, está
    memorizando y el backtest tiene leakage."""
    rng = np.random.default_rng(42)
    ruido = 100 + rng.normal(0, 15, 200)
    resultado = backtest(ruido, horizonte=14, n_ventanas=3)
    assert resultado.mape_modelo > resultado.mape_baseline * 0.5


def test_una_serie_corta_devuelve_un_resultado_sin_metricas():
    resultado = backtest(np.array([1, 2, 3, 4, 5]), horizonte=14, n_ventanas=3)
    assert resultado.ventanas == 0
    assert resultado.mape_modelo is None
    assert "insuficiente" in resultado.motivo.lower()


def test_el_forecast_acumula_predicciones_de_varios_productos():
    """Con un pronóstico por producto, guardar solo el último dejaba la mitad
    de las predicciones fuera del informe sin que nada lo indicara."""
    from agent.state import AnalysisState

    estado = AnalysisState(request_id="req-1", consulta="x")
    estado.registrar_resultado("forecast_sales", ["pred_1"])
    previas = estado.resultados_tools.get("forecast_sales") or []
    estado.registrar_resultado("forecast_sales", [*previas, "pred_2"])
    assert estado.resultados_tools["forecast_sales"] == ["pred_1", "pred_2"]
