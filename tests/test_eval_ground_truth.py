"""Tests de cómo se interroga al agente durante la evaluación.

Lo que se protege acá es la validez del examen. Un eval que menciona la anomalía
en el enunciado —"¿por qué subieron las devoluciones de P010 el 14 de febrero?"—
no mide si el agente detecta algo: mide si sabe repetir lo que se le dijo.

Es el mismo cuidado que motivó el `DENY SELECT` sobre `dbo.ground_truth`. De
nada sirve cerrarle la puerta a la tabla si después la respuesta viaja en la
pregunta.
"""

from __future__ import annotations

from datetime import date

from agent.nodes.planner import _pide_proyeccion
from eval.ground_truth import (
    casos_de_evaluacion,
    consulta_con_proyeccion,
    consulta_para,
)
from eval.metricas import EventoSembrado

CAIDA = EventoSembrado(
    tipo="caida_ventas", product_id="P010", fecha=date(2026, 2, 14),
    magnitud=-42.5, descripcion="Quiebre de stock por campaña sin reposición",
)

PICO = EventoSembrado(
    tipo="pico_devoluciones", product_id="P010", fecha=date(2026, 2, 14),
    magnitud=5.7, descripcion="Cambio de lote del proveedor",
)


def test_la_fecha_que_llega_como_texto_se_convierte_a_date():
    """El driver ODBC devuelve las columnas DATE como string.

    Con un `@dataclass` la anotación `fecha: date` no valida nada y el string
    viajaba intacto hasta reventar tres capas más adelante, en un `strftime`.
    El modelo tiene que rechazar o convertir en el borde, que es donde el dato
    entra — el mismo criterio que sostiene a `Report` y a `AnalysisState`.
    """
    evento = EventoSembrado(
        tipo="pico_devoluciones", product_id="P038", fecha="2025-05-04",
        magnitud=0.42, descripcion="lote defectuoso",
    )

    assert evento.fecha == date(2025, 5, 4)
    assert "2025-05" in consulta_para(evento)


def test_la_consulta_nombra_el_producto_y_el_periodo():
    consulta = consulta_para(CAIDA)

    assert "P010" in consulta
    assert "2026-02" in consulta


def test_la_consulta_no_revela_el_tipo_de_evento():
    consulta = consulta_para(CAIDA).lower()

    assert "caida_ventas" not in consulta
    assert "caída" not in consulta


def test_la_consulta_no_revela_la_descripcion_del_evento():
    """La descripción ES la respuesta. Si viaja en la pregunta, el agente
    solamente tiene que devolverla."""
    consulta = consulta_para(PICO).lower()

    assert "lote" not in consulta
    assert "proveedor" not in consulta


def test_la_consulta_no_revela_la_magnitud():
    consulta = consulta_para(PICO)

    assert "5,7" not in consulta
    assert "5.7" not in consulta


def test_dos_eventos_distintos_producen_la_misma_pregunta():
    """La prueba más fuerte de que el enunciado no depende de la respuesta.

    Mismo producto y mismo mes, anomalías opuestas —una caída de ventas y un
    pico de devoluciones—, y el agente recibe exactamente el mismo texto. Lo
    que cambia es lo que tiene que encontrar, no lo que se le cuenta.
    """
    assert consulta_para(CAIDA) == consulta_para(PICO)


# --- selección de casos: la otra cara de la misma moneda ----------------------
#
# Que dos eventos produzcan la misma pregunta es la garantía de que el enunciado
# no filtra la respuesta. Y es, exactamente por eso, lo que impide contarlos como
# dos casos: el agente recibiría dos veces el mismo texto y el eval anotaría dos
# resultados sobre una sola pregunta.
#
# La corrida del 2026-08-12 lo mostró. Los eventos 3 y 4 eran `pico_ventas` de
# P033 el 09 y el 11 de junio: misma consulta, mismas magnitudes en el informe
# (726 unidades, 83.039 de revenue) y resultados OPUESTOS en
# `usa_la_evidencia_documental` — uno citó `doc_promo_005` y el otro no citó
# nada. Se creyó estar midiendo seis consultas y eran cinco.

MISMO_MES = EventoSembrado(
    tipo="caida_ventas", product_id="P010", fecha=date(2026, 2, 27),
    magnitud=-38.1, descripcion="Segunda caída del mismo mes",
)

OTRO_MES = EventoSembrado(
    tipo="caida_ventas", product_id="P010", fecha=date(2026, 3, 2),
    magnitud=-12.0, descripcion="Caída del mes siguiente",
)


def test_dos_eventos_del_mismo_producto_y_mes_son_un_solo_caso():
    """Repetir la pregunta no agranda la muestra: infla el denominador.

    Seis corridas sobre cinco preguntas no son seis observaciones. La repetida
    pesa doble y la varianza del modelo entra al promedio disfrazada de
    cobertura.
    """
    casos = casos_de_evaluacion([CAIDA, MISMO_MES])

    assert len(casos) == 1
    assert len({consulta_para(c) for c in casos}) == 1


def test_cada_caso_produce_una_consulta_distinta():
    """La invariante que define la función, y la que hay que poder afirmar
    antes de dividir cualquier cosa por la cantidad de casos."""
    casos = casos_de_evaluacion([CAIDA, MISMO_MES, OTRO_MES])

    assert len({consulta_para(c) for c in casos}) == len(casos)


def test_un_producto_y_mes_con_dos_anomalias_opuestas_se_descarta():
    """Acá no alcanza con quedarse con uno: el oráculo sería ambiguo.

    `CAIDA` y `PICO` comparten producto y mes con anomalías de distinto tipo.
    Una sola pregunta no puede distinguirlas, así que cualquier veredicto sobre
    ese caso mediría contra un evento elegido por el orden del `ORDER BY`. Se
    descarta entero: un caso que no se puede juzgar no se juzga.
    """
    assert casos_de_evaluacion([CAIDA, PICO]) == []


def test_el_orden_de_los_casos_es_estable():
    """Mismo motivo que el `ORDER BY` de `leer_eventos`: dos corridas tienen que
    recorrer los mismos casos en la misma secuencia."""
    eventos = [OTRO_MES, CAIDA, MISMO_MES]

    assert casos_de_evaluacion(eventos) == casos_de_evaluacion(eventos)
    assert [c.fecha for c in casos_de_evaluacion(eventos)] == [
        date(2026, 2, 14), date(2026, 3, 2),
    ]


# --- la consulta que ejercita el forecast -------------------------------------
#
# `no_invierte_el_sentido_del_error` estuvo cuatro corridas marcada NUNCA
# APLICÓ, y la causa no era el agente: era el enunciado. El planner solo
# planifica `forecast_sales` si la consulta pide una proyección
# (`planner.PIDE_PROYECCION`), y `consulta_para` no menciona nada de eso. El
# agente hacía lo correcto —no entrenar un modelo para quien solo pidió KPIs— y
# la métrica juzgaba un informe que nunca podía existir.

def test_la_consulta_de_proyeccion_activa_el_plan_de_forecast():
    """El test que impide que esto vuelva a pasar en silencio.

    Ata el enunciado del eval al criterio real del planner. Si alguien edita
    `PIDE_PROYECCION`, esto falla acá y no cuatro corridas después, cuando la
    métrica vuelva a informar que no juzgó nada.
    """
    assert _pide_proyeccion(consulta_con_proyeccion(CAIDA))
    assert not _pide_proyeccion(consulta_para(CAIDA))


def test_la_consulta_de_proyeccion_sigue_sin_revelar_el_evento():
    """La disciplina del enunciado no se afloja porque el caso sea otro."""
    consulta = consulta_con_proyeccion(PICO).lower()

    assert "lote" not in consulta
    assert "proveedor" not in consulta
    assert "5,7" not in consulta
    assert "pico_devoluciones" not in consulta


def test_la_consulta_de_proyeccion_conserva_el_producto_y_el_periodo():
    consulta = consulta_con_proyeccion(CAIDA)

    assert "P010" in consulta
    assert "2026-02" in consulta


def test_las_dos_consultas_del_mismo_evento_son_distintas():
    """Si fueran iguales, los casos de proyección serían repeticiones y volvería
    el problema del denominador inflado que la deduplicación vino a resolver."""
    assert consulta_con_proyeccion(CAIDA) != consulta_para(CAIDA)


def test_la_consulta_pide_explicar_lo_anomalo_sin_afirmar_que_lo_hay():
    """Se invita a mirar, no se avisa que hay algo.

    Si la consigna afirmara que hubo una anomalía, el agente encontraría una
    aunque no existiera — y no se podría medir el falso positivo.
    """
    consulta = consulta_para(PICO).lower()

    assert "si ves algo fuera de lo normal" in consulta
