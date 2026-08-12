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


def _informe(afirmaciones=None, metricas=None, docs=()) -> Report:
    """`docs` declara fuentes documentales: `Report` rechaza toda cita a una
    fuente no declarada, así que citar un documento exige declararlo primero."""
    return Report(
        request_id="req-001", consulta="x", generado_en=datetime(2026, 8, 10, 12, 0),
        modelo_llm="llama3.2:3b",
        fuentes=[
            Fuente(id=FUENTE_SQL, tipo="sql", referencia="dbo.order_items",
                   consultada_en=datetime(2026, 8, 10, 11, 59)),
            *[Fuente(id=d, tipo="documento", referencia=f"{d}.pdf",
                     consultada_en=datetime(2026, 8, 10, 11, 59)) for d in docs],
        ],
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


# --- La cifra que sostiene el porqué vive en el documento --------------------
#
# Medido el 2026-08-12: el sintetizador citaba `doc_promo_001` en 3 de 3
# corridas de P016 y el eval end-to-end registraba ese caso como fallado. En el
# medio estaba este nodo.
#
#   ANTES:   [doc_promo_001] La campaña de descuento del 30% ... explicó ...
#   DESPUÉS: — eliminada —
#
# El 30 sale del documento y no de las métricas, así que el validador lo leía
# como cifra inventada y borraba la afirmación. Con ella se iba la cita, y el
# informe perdía la única frase que decía POR QUÉ.
#
# El control lo confirma por el otro lado: en P038 la conclusión citada decía
# "11,7% de las unidades devueltas", que ES la tasa de devolución de las
# métricas, y sobrevivía. Nunca fue el tipo de evento: es de dónde sale la cifra.
#
# Un documento recuperado es una fuente, y un número que figura textualmente en
# un pasaje citado es rastreable. Que es todo lo que el proyecto le pide a un
# número.

FUENTE_DOC = "doc_promo_001"

EVIDENCIA = [{
    "doc_id": FUENTE_DOC,
    "texto": "La acción combinó un descuento del 30% con pauta en redes. "
             "La demanda diaria se multiplicó aproximadamente por 3.",
}]


def test_acepta_una_cifra_que_figura_en_el_documento_citado():
    informe = _informe([
        Afirmacion(texto="La campaña de descuento del 30% explica el salto",
                   tipo="hecho", fuentes=[FUENTE_DOC])
    ], docs=[FUENTE_DOC])

    resultado = validar_informe(
        informe, {"product_metrics": {"P001": _metrica()}}, evidencia=EVIDENCIA)

    assert resultado.afirmaciones_rechazadas == []
    assert [a.texto for a in resultado.informe.resumen_ejecutivo] == [
        "La campaña de descuento del 30% explica el salto"
    ]


def test_la_cita_es_lo_que_habilita_la_cifra():
    """Sin cita no hay permiso. Si cualquier número del corpus quedara
    disponible para cualquier afirmación, el guardrail se aflojaría de más: una
    frase sin fuente podría tomar prestada una cifra que nunca miró."""
    informe = _informe([
        Afirmacion(texto="El descuento del 30% explica el salto",
                   tipo="hecho", fuentes=[FUENTE_SQL])
    ])

    resultado = validar_informe(
        informe, {"product_metrics": {"P001": _metrica()}}, evidencia=EVIDENCIA)

    assert resultado.informe.resumen_ejecutivo == []
    assert "30" in resultado.afirmaciones_rechazadas[0]


def test_citar_un_documento_no_habilita_las_cifras_de_otro():
    """El permiso es por documento, no por informe."""
    otro = [{"doc_id": "doc_prov_009", "texto": "Se detectaron desvíos en el 30% del lote."}]
    informe = _informe([
        Afirmacion(texto="La campaña de descuento del 30% explica el salto",
                   tipo="hecho", fuentes=[FUENTE_DOC])
    ], docs=[FUENTE_DOC])

    resultado = validar_informe(
        informe, {"product_metrics": {"P001": _metrica()}}, evidencia=otro)

    assert resultado.informe.resumen_ejecutivo == []


def test_sin_evidencia_el_validador_se_comporta_igual_que_antes():
    """La contraprueba: el permiso nuevo no debilita nada por sí solo."""
    informe = _informe([
        Afirmacion(texto="La campaña de descuento del 30% explica el salto",
                   tipo="hecho", fuentes=[FUENTE_DOC])
    ], docs=[FUENTE_DOC])

    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})

    assert resultado.informe.resumen_ejecutivo == []


def test_una_cifra_que_no_esta_ni_en_los_datos_ni_en_el_documento_se_rechaza():
    """Lo que el nodo vino a atrapar sigue atrapado: el documento amplía las
    fuentes legítimas, no las suspende."""
    informe = _informe([
        Afirmacion(texto="La campaña de descuento del 55% explica el salto",
                   tipo="hecho", fuentes=[FUENTE_DOC])
    ], docs=[FUENTE_DOC])

    resultado = validar_informe(
        informe, {"product_metrics": {"P001": _metrica()}}, evidencia=EVIDENCIA)

    assert resultado.informe.resumen_ejecutivo == []
    assert "55" in resultado.afirmaciones_rechazadas[0]


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
    assert any("respaldo" in w.lower() or "descart" in w.lower()
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


# --- Segunda capa: la comparación se sostiene --------------------------------

def test_rechaza_una_comparacion_invertida_con_numeros_reales():
    """El caso exacto que produjo el modelo en una corrida real.

    Ambas cifras salen de SQL, así que la verificación numérica las aprueba.
    Lo que está mal es la relación que el texto afirma sobre ellas: 242 no
    lidera sobre 257.
    """
    informe = _informe(
        [Afirmacion(texto="Ribera lidera en unidades con 242, frente a las 257 "
                          "de Calma", tipo="hecho", fuentes=[FUENTE_SQL])],
        metricas=[_metrica("P002", unidades=242), _metrica("P003", unidades=257)],
    )
    resultado = validar_informe(informe, {"product_metrics": {
        "P002": _metrica("P002", unidades=242),
        "P003": _metrica("P003", unidades=257),
    }})
    assert not resultado.aprobado
    assert resultado.informe.resumen_ejecutivo == []
    assert any("aritm" in w.lower() for w in resultado.informe.advertencias)


def test_acepta_una_comparacion_correcta():
    informe = _informe(
        [Afirmacion(texto="Calma lidera en unidades con 257, frente a las 242 "
                          "de Ribera", tipo="hecho", fuentes=[FUENTE_SQL])],
        metricas=[_metrica("P002", unidades=242)],
    )
    resultado = validar_informe(informe, {"product_metrics": {
        "P002": _metrica("P002", unidades=242),
        "P003": _metrica("P003", unidades=257),
    }})
    assert resultado.aprobado
    assert len(resultado.informe.resumen_ejecutivo) == 1


def test_acepta_un_comparativo_de_inferioridad_correcto():
    """El validador no puede borrar afirmaciones verdaderas.

    Con "más baja" la relación esperada se invierte, y 3,7 < 7,1 la cumple.
    """
    informe = _informe(
        [Afirmacion(texto="Calma tiene una tasa más baja con 3,7%, frente a "
                          "las 7,1% de Ribera", tipo="hecho", fuentes=[FUENTE_SQL])],
        metricas=[_metrica("P002", tasa_devolucion_pct=3.7)],
    )
    resultado = validar_informe(informe, {"product_metrics": {
        "P002": _metrica("P002", tasa_devolucion_pct=3.7),
        "P003": _metrica("P003", tasa_devolucion_pct=7.1),
    }})
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


# --- Formatos numéricos mezclados --------------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    # Formato español: punto de miles, coma decimal.
    ("Vendió 1.243 unidades", 1243.0),
    ("El margen fue 31,2%", 31.2),
    ("Facturó 87.010,50", 87010.50),
    # Formato inglés: coma de miles, punto decimal. El modelo lo produce, y
    # leerlo como español convertía 62.461,52 en 62,46 — y el validador
    # descartaba una afirmación CORRECTA por no saber leer el número.
    ("La recaudación fue de 62,461.52 dólares", 62461.52),
    ("Vendió 1,243 unidades", 1243.0),
    ("Total de 1,234,567.89", 1234567.89),
    # Sin separadores.
    ("Fueron 423 unidades", 423.0),
    ("El valor es 0.5", 0.5),
])
def test_interpreta_numeros_en_ambos_formatos(texto, esperado):
    assert esperado in extraer_numeros_de_negocio(texto)


def test_no_descarta_una_afirmacion_por_el_formato_del_numero():
    """El caso real: el modelo escribió en formato inglés y el validador la
    eliminó. Un falso positivo del validador borra información correcta."""
    informe = _informe([
        Afirmacion(texto="La recaudación total fue de 62,461.52 dólares",
                   tipo="hecho", fuentes=[FUENTE_SQL])
    ])
    resultado = validar_informe(
        informe, {"product_metrics": {"P001": _metrica(revenue=62461.52)}}
    )
    assert resultado.aprobado, resultado.afirmaciones_rechazadas


def test_ignora_identificadores_alfanumericos_de_cualquier_tipo():
    """`L1829` es un número de lote, no una magnitud.

    Es el mismo falso positivo que tenían `doc_112` y `§3.2`, con otro prefijo.
    En vez de agregar un patrón por cada tipo que aparezca, se generaliza: una
    letra pegada a dígitos es un identificador, no una cantidad.
    """
    assert extraer_numeros_de_negocio(
        "defectos detectados en el lote L1829 del proveedor"
    ) == set()


def test_una_afirmacion_con_un_numero_de_lote_no_se_descarta():
    informe = _informe([
        Afirmacion(texto="Las devoluciones se relacionan con defectos "
                         "detectados en el lote L1829", tipo="hecho",
                   fuentes=[FUENTE_SQL])
    ])
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})
    assert resultado.aprobado, resultado.afirmaciones_rechazadas


def test_sigue_detectando_magnitudes_junto_a_identificadores():
    """Contraprueba: generalizar el filtro no puede cegar al validador."""
    numeros = extraer_numeros_de_negocio(
        "El lote L1829 del P002 acumuló 1.243 devoluciones"
    )
    assert numeros == {1243.0}


# --- Recomendaciones disfrazadas de hecho ------------------------------------

@pytest.mark.parametrize("texto", [
    "Se recomienda revisar el stock del lote antes de seguir vendiendo",
    "Habría que bajar el precio del producto",
    "Conviene auditar el proceso del proveedor",
    "Sería conveniente revisar la política de descuentos",
    "Se sugiere reforzar el control de calidad",
])
def test_detecta_una_recomendacion_redactada_como_hecho(texto):
    """El modelo etiqueta mal y el informe no lo nota.

    `Report` valida la ETIQUETA, no el texto: una recomendación marcada como
    "hecho" pasa el control y se muestra junto a los datos verificados. Ahí se
    borra la frontera entre lo que pasó y lo que alguien opina que habría que
    hacer, que es justamente lo que el informe existe para separar.
    """
    from agent.nodes.validator import parece_recomendacion
    assert parece_recomendacion(texto)


@pytest.mark.parametrize("texto", [
    "El margen del P002 fue de 31,2%",
    "Las devoluciones aumentaron en el período",
    "El proveedor reportó defectos en el lote",
])
def test_no_confunde_un_hecho_con_una_recomendacion(texto):
    from agent.nodes.validator import parece_recomendacion
    assert not parece_recomendacion(texto)


def test_una_recomendacion_mal_etiquetada_se_mueve_a_su_seccion():
    """No se descarta: se reubica. La información es útil, el problema es dónde
    estaba puesta."""
    informe = _informe([
        Afirmacion(texto="El margen fue de 31,2%", tipo="hecho",
                   fuentes=[FUENTE_SQL]),
        Afirmacion(texto="Se recomienda revisar el stock del lote",
                   tipo="hecho", fuentes=[FUENTE_SQL]),
    ])
    resultado = validar_informe(informe, {"product_metrics": {"P001": _metrica()}})

    assert len(resultado.informe.resumen_ejecutivo) == 1
    assert len(resultado.informe.recomendaciones) == 1
    assert resultado.informe.recomendaciones[0].tipo == "recomendacion"
