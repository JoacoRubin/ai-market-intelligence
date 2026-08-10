"""Tests del nodo ReportValidator.

Es el último nodo del grafo y el que decide si lo que el modelo escribió puede
salir. Es determinístico: verificar números es aritmética, no comprensión.

Dos lecciones del primer día del proyecto están codificadas acá.

**La primera**: el LLM puede escribir un informe con todos los números correctos
y aun así estar mal. Un validador numérico es NECESARIO pero NO SUFICIENTE — no
detecta interpretaciones invertidas ni evidencia sin usar. Lo que sí detecta, y
es lo grave, es la cifra inventada.

**La segunda**: el prototipo del auditor tenía un bug propio. Contaba como
claims numéricos los identificadores de documento (`doc_112` → 112) y los
números de sección (`§3.2` → 3.2), inflando la métrica cerca del doble. Un eval
con falsos positivos es peor que no tener eval: da confianza donde no la hay.
Por eso hay tests específicos para eso.
"""

from datetime import datetime

import pytest

from agent.nodes.validator import extraer_numeros_de_negocio, validar_informe
from core.report import Afirmacion, Fuente, MetricaProducto, Report

FUENTE_SQL = "sql:product_metrics"


def _metrica(pid="P001", **kw) -> MetricaProducto:
    base = dict(product_id=pid, nombre="Alfa", unidades=1243, revenue=87010.0,
                margen_pct=31.2, crecimiento_pct=18.4, tasa_devolucion_pct=2.1,
                fuente=FUENTE_SQL)
    base.update(kw)
    return MetricaProducto(**base)


def _informe(afirmaciones=None, metricas=None) -> Report:
    return Report(
        request_id="req-001", consulta="x", generado_en=datetime(2026, 8, 10, 12, 0),
        modelo_llm="llama3.2:3b",
        fuentes=[Fuente(id=FUENTE_SQL, tipo="sql", referencia="dbo.order_items",
                        consultada_en=datetime(2026, 8, 10, 11, 59))],
        resumen_ejecutivo=afirmaciones or [],
        metricas=metricas if metricas is not None else [_metrica()],
    )


# --- Extracción de números: el bug del auditor v1 ---------------------------

def test_ignora_identificadores_de_documento():
    """`doc_112` no es un claim numérico: es una referencia.

    Contarlo inflaba la métrica de groundedness cerca del doble en el prototipo.
    """
    assert extraer_numeros_de_negocio("Según doc_112 y doc_087, la tendencia") == set()


def test_ignora_numeros_de_seccion():
    assert extraer_numeros_de_negocio("Ver §3.2 y §1.1 del reporte") == set()


def test_ignora_identificadores_de_producto():
    """`P001` contiene un 001 que no es una cantidad."""
    assert extraer_numeros_de_negocio("El P001 supera al P002") == set()


def test_ignora_versiones_de_modelo():
    assert extraer_numeros_de_negocio("Generado con llama3.2:3b y sales_v3") == set()


def test_extrae_las_magnitudes_reales():
    numeros = extraer_numeros_de_negocio(
        "Vendió 1.243 unidades por USD 87.010,00 con 31,2% de margen"
    )
    assert numeros == {1243.0, 87010.0, 31.2}


def test_maneja_negativos():
    assert -3.1 in extraer_numeros_de_negocio("Cayó -3,1% en el período")


def test_combina_referencias_y_magnitudes():
    """El caso realista: una afirmación con cita y cifras."""
    numeros = extraer_numeros_de_negocio(
        "El P002 (ver doc_112 §3.2) devolvió 5,7% de las unidades"
    )
    assert numeros == {5.7}


# --- Detección de números inventados ----------------------------------------

def test_aprueba_un_informe_con_numeros_respaldados():
    informe = _informe([
        Afirmacion(texto="Alfa vendió 1.243 unidades con 31,2% de margen",
                   tipo="hecho", fuentes=[FUENTE_SQL])
    ])
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})
    assert resultado.aprobado
    assert resultado.afirmaciones_rechazadas == []


def test_rechaza_una_afirmacion_con_un_numero_inventado():
    """El caso que el validador existe para atrapar.

    El modelo escribe una cifra plausible que no salió de ninguna herramienta.
    Es indistinguible de un dato real para el lector, y por eso es peligrosa.
    """
    informe = _informe([
        Afirmacion(texto="Alfa vendió 9.999 unidades", tipo="hecho",
                   fuentes=[FUENTE_SQL])
    ])
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})
    assert not resultado.aprobado
    assert len(resultado.afirmaciones_rechazadas) == 1
    assert "9999" in str(resultado.afirmaciones_rechazadas[0]) or \
           "9.999" in str(resultado.afirmaciones_rechazadas[0])


def test_una_afirmacion_rechazada_no_llega_al_informe():
    """No alcanza con advertir: la afirmación falsa se saca.

    Dejarla con una nota al pie confía en que alguien lea la nota. El informe
    tiene que ser correcto por sí mismo.
    """
    informe = _informe([
        Afirmacion(texto="Alfa vendió 1.243 unidades", tipo="hecho",
                   fuentes=[FUENTE_SQL]),
        Afirmacion(texto="Alfa proyecta 5.000 unidades", tipo="hecho",
                   fuentes=[FUENTE_SQL]),
    ])
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})
    assert len(resultado.informe.resumen_ejecutivo) == 1
    assert "1.243" in resultado.informe.resumen_ejecutivo[0].texto


def test_el_rechazo_queda_documentado_en_las_advertencias():
    """Una corrección silenciosa es una corrección que nadie puede auditar."""
    informe = _informe([
        Afirmacion(texto="Alfa vendió 9.999 unidades", tipo="hecho",
                   fuentes=[FUENTE_SQL])
    ])
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})
    assert any("no respaldad" in w.lower() or "descart" in w.lower()
               for w in resultado.informe.advertencias), resultado.informe.advertencias


def test_las_recomendaciones_no_se_validan_numericamente():
    """Una recomendación es un juicio, no un dato. Exigirle respaldo numérico
    la eliminaría siempre, y el informe perdería su parte accionable."""
    informe = _informe()
    informe.recomendaciones = [
        Afirmacion(texto="Revisar el stock antes de la próxima campaña",
                   tipo="recomendacion")
    ]
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})
    assert len(resultado.informe.recomendaciones) == 1


def test_tolera_diferencias_de_redondeo():
    """El modelo puede escribir 31,2% donde la métrica dice 31,23%. Eso es
    redondeo, no invención: rechazarlo vaciaría informes correctos."""
    informe = _informe([
        Afirmacion(texto="El margen fue de 31,2%", tipo="hecho",
                   fuentes=[FUENTE_SQL])
    ])
    resultado = validar_informe(
        informe, {"product_metrics": {"P001": _metrica(margen_pct=31.23)}}
    )
    assert resultado.aprobado


# --- Sin datos ---------------------------------------------------------------

def test_sin_resultados_de_herramientas_se_rechaza_todo_hecho():
    """Si no hubo datos, ninguna afirmación factual puede estar respaldada.

    Es el escenario donde el LLM más inventa: sin información, igual encuentra
    algo para decir.
    """
    informe = _informe([
        Afirmacion(texto="Alfa lidera con 1.243 unidades", tipo="hecho",
                   fuentes=[FUENTE_SQL])
    ])
    resultado = validar_informe(informe, {})
    assert not resultado.aprobado
    assert resultado.informe.resumen_ejecutivo == []


def test_un_informe_sin_afirmaciones_se_aprueba():
    resultado = validar_informe(_informe(), {"product_metrics": {"P001": _metrica()}})
    assert resultado.aprobado


# --- Métrica de groundedness -------------------------------------------------

def test_reporta_la_tasa_de_groundedness():
    informe = _informe([
        Afirmacion(texto="Vendió 1.243 unidades", tipo="hecho", fuentes=[FUENTE_SQL]),
        Afirmacion(texto="Vendió 9.999 unidades", tipo="hecho", fuentes=[FUENTE_SQL]),
    ])
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})
    assert resultado.groundedness == pytest.approx(0.5)


def test_groundedness_de_un_informe_sin_numeros_es_uno():
    """Sin cifras que verificar, no hay nada que pueda estar inventado."""
    informe = _informe([
        Afirmacion(texto="El producto muestra una tendencia favorable",
                   tipo="hecho", fuentes=[FUENTE_SQL])
    ])
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})
    assert resultado.groundedness == 1.0
