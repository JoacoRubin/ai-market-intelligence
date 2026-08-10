"""Tests de integración del grafo completo.

Ejercitan el flujo entero —router, planificación, ejecución, compuerta de
evidencia, síntesis y validación— con un doble del modelo. Corren en
milisegundos, así que pueden vivir en la suite de cada commit.

Lo que se verifica acá no es que el modelo acierte (eso lo mide el golden set)
sino que **el grafo se comporte**: que corte cuando no hay nada que analizar,
que replanifique una cantidad acotada de veces, que degrade cuando el modelo
falla y que ninguna cifra inventada llegue al informe final.
"""

from datetime import date, datetime

import pytest

from agent.graph import analizar
from agent.llm import ClienteFalso, ClienteQueFalla
from agent.state import Intencion
from core.db import hay_base_disponible

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not hay_base_disponible(),
                       reason="SQL Server no está levantado"),
]

HOY = date(2026, 3, 31)
AHORA = datetime(2026, 8, 10, 12, 0)

CONSULTA = "Compará P002 y P003 en los últimos 90 días"


def _router(intencion="product_performance", dias=90):
    return {"intencion": intencion, "dias": dias}


def _sintesis(*conclusiones):
    return {"conclusiones": list(conclusiones)}


# --- Camino completo ---------------------------------------------------------

def test_flujo_completo_produce_un_informe():
    cliente = ClienteFalso([
        _router(),
        _sintesis("El producto analizado muestra actividad en el período"),
    ])
    estado = analizar(CONSULTA, cliente, hoy=HOY, ahora=AHORA)

    assert estado.intencion == Intencion.PRODUCT_PERFORMANCE
    assert estado.entidades == ["P002", "P003"]
    assert estado.informe is not None
    assert len(estado.informe.metricas) == 2


def test_el_trace_registra_todas_las_etapas():
    """"Cómo se obtuvo" se arma con esto: sin trace no hay auditoría posible."""
    cliente = ClienteFalso([_router(), _sintesis("Actividad registrada")])
    estado = analizar(CONSULTA, cliente, hoy=HOY, ahora=AHORA)

    nodos = [p.nodo for p in estado.trace]
    for esperado in ("router", "planner", "sql_tool", "synthesizer"):
        assert esperado in nodos, f"falta {esperado} en el trace: {nodos}"


def test_los_kpis_del_informe_salen_de_la_base():
    cliente = ClienteFalso([_router(), _sintesis("Actividad registrada")])
    estado = analizar(CONSULTA, cliente, hoy=HOY, ahora=AHORA)

    for m in estado.informe.metricas:
        assert m.fuente.startswith("sql:")
        assert m.unidades >= 0


# --- Corte temprano ----------------------------------------------------------

def test_una_consulta_fuera_de_alcance_no_llega_a_consultar_la_base():
    """Seguir con lo que no se puede analizar solo gasta CPU para llegar a un
    informe vacío."""
    cliente = ClienteFalso([_router(intencion="fuera_de_alcance", dias=0)])
    estado = analizar("Contame un chiste", cliente, hoy=HOY, ahora=AHORA)

    assert estado.intencion == Intencion.FUERA_DE_ALCANCE
    assert estado.informe is None
    assert estado.llamadas_tools == 0


def test_una_consulta_sin_productos_corta_antes_de_ejecutar():
    cliente = ClienteFalso([_router(intencion="product_performance")])
    estado = analizar("Compará las ventas del mes", cliente, hoy=HOY, ahora=AHORA)
    assert estado.informe is None


# --- Límites -----------------------------------------------------------------

def test_sin_datos_replanifica_y_termina_sin_loop_infinito():
    """La defensa central del agente.

    El producto P999 no existe, así que ninguna cantidad de replanificaciones va
    a encontrar datos. El grafo tiene que rendirse: rendirse es una salida
    legítima, insistir para siempre no.
    """
    cliente = ClienteFalso([_router()] + [_router()] * 4)
    estado = analizar("Analizá el P999 de los últimos 30 días", cliente,
                      hoy=HOY, ahora=AHORA)

    assert estado.reintentos <= estado.max_reintentos + 1
    assert any("intento" in w.lower() or "replan" in w.lower()
               for w in estado.advertencias), estado.advertencias


def test_el_presupuesto_de_herramientas_se_respeta():
    cliente = ClienteFalso([_router()] + [_router()] * 4)
    estado = analizar("Analizá el P999 de los últimos 30 días", cliente,
                      hoy=HOY, ahora=AHORA)
    assert estado.llamadas_tools <= estado.max_llamadas_tools


# --- Degradación -------------------------------------------------------------

def test_con_el_modelo_caido_el_grafo_termina_sin_romperse():
    estado = analizar(CONSULTA, ClienteQueFalla(), hoy=HOY, ahora=AHORA)
    assert estado.intencion == Intencion.FUERA_DE_ALCANCE
    assert estado.error is not None


def test_si_el_modelo_falla_al_redactar_se_usa_el_respaldo_deterministico():
    """El modelo responde el routing y después se queda sin respuestas.

    El informe sale igual, armado con reglas sobre los mismos números. Que el
    sistema produzca un resultado válido sin el LLM es lo que lo hace no
    depender del LLM para tener razón.
    """
    cliente = ClienteFalso([_router()])  # sin respuesta para la síntesis
    estado = analizar(CONSULTA, cliente, hoy=HOY, ahora=AHORA)

    assert estado.informe is not None
    assert len(estado.informe.resumen_ejecutivo) > 0
    assert "respaldo" in estado.informe.modelo_llm.lower()


# --- El validador dentro del grafo -------------------------------------------

def test_una_cifra_inventada_por_el_modelo_no_llega_al_informe():
    """El escenario que el proyecto entero existe para evitar.

    El modelo redacta una conclusión con un número que ninguna herramienta
    produjo. El validador la elimina antes de que salga.
    """
    cliente = ClienteFalso([
        _router(),
        _sintesis("El producto vendió 999.999 unidades en el período"),
    ])
    estado = analizar(CONSULTA, cliente, hoy=HOY, ahora=AHORA)

    texto = " ".join(a.texto for a in estado.informe.resumen_ejecutivo)
    assert "999.999" not in texto
    assert any("respaldo" in w.lower() or "descart" in w.lower()
               for w in estado.informe.advertencias), estado.informe.advertencias


def test_una_conclusion_respaldada_sobrevive_a_la_validacion():
    """Contraprueba: el validador no vacía informes correctos."""
    cliente = ClienteFalso([
        _router(),
        _sintesis("El período muestra actividad comercial sostenida"),
    ])
    estado = analizar(CONSULTA, cliente, hoy=HOY, ahora=AHORA)
    assert len(estado.informe.resumen_ejecutivo) == 1


# --- El informe resultante es válido -----------------------------------------

def test_el_informe_cumple_los_invariantes_del_modelo():
    """Si el grafo produjera un informe que viola la trazabilidad, Pydantic
    habría fallado al construirlo. Que exista ya es la prueba."""
    cliente = ClienteFalso([_router(), _sintesis("Actividad registrada")])
    estado = analizar(CONSULTA, cliente, hoy=HOY, ahora=AHORA)

    informe = estado.informe
    assert informe.modelo_llm
    assert informe.fuentes
    for a in informe.resumen_ejecutivo:
        assert a.tipo == "hecho"
        assert a.fuentes


def test_el_informe_se_puede_renderizar_a_pdf(tmp_path):
    """Integración de punta a punta: del lenguaje natural al PDF descargable."""
    from core.report_pdf import render_pdf

    cliente = ClienteFalso([_router(), _sintesis("Actividad registrada")])
    estado = analizar(CONSULTA, cliente, hoy=HOY, ahora=AHORA)

    destino = render_pdf(estado.informe, tmp_path / "informe.pdf")
    assert destino.read_bytes().startswith(b"%PDF-")
