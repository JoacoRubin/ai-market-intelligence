"""Tests del modelo `Report`, la fuente única de verdad del informe.

El modelo no es un contenedor de datos: es el punto donde el sistema se defiende.
Un informe que viola las reglas de trazabilidad **no se puede construir**.

Por qué acá y no en el ReportValidator: el validador corre al final del grafo y
puede tener bugs. Pydantic corre en el constructor, siempre, sin que nadie se
acuerde de llamarlo. Si un nodo del grafo arma un informe con un número sin
fuente, revienta ahí mismo — no tres pasos después, cuando ya es un PDF que
alguien mandó por mail.
"""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from core.report import (
    Afirmacion,
    Anomalia,
    Fuente,
    MetricaProducto,
    Prediccion,
    Report,
)


def _fuente(id_: str = "doc_112") -> Fuente:
    return Fuente(
        id=id_,
        tipo="documento",
        referencia="Reporte interno de campañas Q1",
        consultada_en=datetime(2026, 8, 9, 14, 30),
        seccion="§3.2",
    )


def _metrica(pid: str = "P001") -> MetricaProducto:
    return MetricaProducto(
        product_id=pid,
        nombre="Producto A",
        unidades=1243,
        revenue=87010.00,
        margen_pct=31.2,
        crecimiento_pct=18.4,
        tasa_devolucion_pct=2.1,
        fuente="sql:product_metrics",
    )


def _report_minimo(**kw) -> Report:
    base = dict(
        request_id="req-001",
        consulta="Compará Producto A vs B en los últimos 30 días",
        generado_en=datetime(2026, 8, 9, 15, 0),
        modelo_llm="llama3.2:3b",
        metricas=[_metrica()],
        fuentes=[_fuente("sql:product_metrics")],
    )
    base.update(kw)
    return Report(**base)


# --- Trazabilidad: la regla que sostiene todo -------------------------------

def test_un_hecho_sin_fuente_es_rechazado():
    """Un hecho sin respaldo es exactamente lo que el sistema debe impedir.

    Si esto se pudiera construir, el LLM podría afirmar cualquier cosa y el
    informe la mostraría con el mismo aspecto que un dato verificado.
    """
    with pytest.raises(ValidationError, match="fuente"):
        _report_minimo(
            resumen_ejecutivo=[
                Afirmacion(texto="El Producto A lidera en unidades", tipo="hecho")
            ]
        )


def test_un_hecho_con_fuente_inexistente_es_rechazado():
    """Citar una fuente que no está en la lista de fuentes es peor que no citar:
    aparenta rigor y no lo tiene."""
    with pytest.raises(ValidationError, match=r"no declarada|inexistente"):
        _report_minimo(
            resumen_ejecutivo=[
                Afirmacion(
                    texto="El Producto A lidera en unidades",
                    tipo="hecho",
                    fuentes=["doc_999"],
                )
            ]
        )


def test_una_recomendacion_no_requiere_fuente():
    """Una recomendación es un juicio derivado, no un dato. Puede no tener
    fuente directa — pero el informe la marca como recomendación, y por eso
    el lector sabe que no es un hecho."""
    r = _report_minimo(
        recomendaciones=[
            Afirmacion(texto="Revisar el control de stock por talle",
                       tipo="recomendacion")
        ]
    )
    assert r.recomendaciones[0].tipo == "recomendacion"


def test_el_modelo_llm_es_obligatorio():
    """Sin saber qué modelo lo escribió, el informe no es reproducible ni
    auditable."""
    with pytest.raises(ValidationError):
        Report(
            request_id="req-002",
            consulta="x",
            generado_en=datetime(2026, 8, 9, 15, 0),
            metricas=[_metrica()],
            fuentes=[_fuente("sql:product_metrics")],
        )


# --- Predicciones -----------------------------------------------------------

def test_prediccion_sin_backtest_genera_advertencia_automatica():
    """Un forecast sin baseline ni backtesting no se puede interpretar.

    No se rechaza —a veces no hay histórico suficiente— pero el informe DEBE
    advertirlo. Un número de predicción sin margen de error se lee como si
    fuera un hecho.
    """
    r = _report_minimo(
        predicciones=[
            Prediccion(product_id="P001", horizonte_dias=30, valor=1470.0)
        ]
    )
    assert any("backtest" in w.lower() or "baseline" in w.lower()
               for w in r.advertencias), r.advertencias


def test_prediccion_peor_que_el_baseline_genera_advertencia():
    """Si el modelo pierde contra el baseline naïve, el informe tiene que decirlo.
    Presentar como predicción algo peor que 'repetir el último valor' es
    directamente engañoso."""
    r = _report_minimo(
        predicciones=[
            Prediccion(product_id="P001", horizonte_dias=30, valor=1470.0,
                       mape_backtest=14.9, mape_baseline=12.4,
                       modelo_version="sales_v3")
        ]
    )
    assert any("baseline" in w.lower() for w in r.advertencias), r.advertencias


def test_prediccion_mejor_que_baseline_no_genera_advertencia():
    r = _report_minimo(
        predicciones=[
            Prediccion(product_id="P001", horizonte_dias=30, valor=1470.0,
                       mape_backtest=8.3, mape_baseline=14.1,
                       modelo_version="sales_v3")
        ]
    )
    assert not any("baseline" in w.lower() for w in r.advertencias)


# --- Separación de naturaleza -----------------------------------------------

def test_el_resumen_no_admite_recomendaciones():
    """El Executive Summary reporta lo que pasó. Mezclar ahí una sugerencia de
    acción borra la frontera entre lo que ocurrió y lo que alguien opina que
    habría que hacer."""
    with pytest.raises(ValidationError):
        _report_minimo(
            resumen_ejecutivo=[
                Afirmacion(texto="Habría que bajar el precio", tipo="recomendacion")
            ]
        )


def test_las_recomendaciones_no_admiten_hechos():
    with pytest.raises(ValidationError):
        _report_minimo(
            recomendaciones=[
                Afirmacion(texto="El margen fue 31,2%", tipo="hecho",
                           fuentes=["sql:product_metrics"])
            ]
        )


# --- Anomalías --------------------------------------------------------------

def test_anomalia_con_evidencia_valida_se_construye():
    r = _report_minimo(
        fuentes=[_fuente("sql:product_metrics"), _fuente("doc_112")],
        anomalias=[
            Anomalia(
                product_id="P002",
                fecha=date(2026, 1, 18),
                tipo="pico_devoluciones",
                desvios=3.4,
                descripcion="Pico de devoluciones fuera de patrón",
                evidencia=["doc_112"],
            )
        ],
    )
    assert r.anomalias[0].desvios == 3.4


def test_anomalia_con_evidencia_inexistente_es_rechazada():
    with pytest.raises(ValidationError, match=r"no declarada|inexistente"):
        _report_minimo(
            anomalias=[
                Anomalia(product_id="P002", fecha=date(2026, 1, 18),
                         tipo="pico_devoluciones", desvios=3.4,
                         descripcion="x", evidencia=["doc_inexistente"])
            ]
        )


# --- Caso completo ----------------------------------------------------------

def test_informe_completo_valido():
    r = _report_minimo(
        fuentes=[_fuente("sql:product_metrics"), _fuente("doc_112")],
        resumen_ejecutivo=[
            Afirmacion(texto="El Producto A lidera en crecimiento",
                       tipo="hecho", fuentes=["sql:product_metrics"])
        ],
        predicciones=[
            Prediccion(product_id="P001", horizonte_dias=30, valor=1470.0,
                       mape_backtest=8.3, mape_baseline=14.1,
                       modelo_version="sales_v3")
        ],
        anomalias=[
            Anomalia(product_id="P002", fecha=date(2026, 1, 18),
                     tipo="pico_devoluciones", desvios=3.4,
                     descripcion="Pico fuera de patrón", evidencia=["doc_112"])
        ],
        recomendaciones=[
            Afirmacion(texto="Revisar control de stock por talle",
                       tipo="recomendacion")
        ],
    )
    assert r.modelo_llm == "llama3.2:3b"
    assert len(r.fuentes) == 2
    assert r.advertencias == []
