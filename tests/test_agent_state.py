"""Tests del estado del agente y sus límites.

El estado es lo que viaja entre los nodos del grafo. Que sea tipado no es
prolijidad: es lo que permite que un nodo confíe en lo que recibió sin volver a
validarlo, y que un error aparezca donde se produjo y no tres pasos después.

Los límites (`max_reintentos`, `max_llamadas_tools`) son la defensa contra el
problema clásico de los agentes: el loop infinito. Un agente sin techo puede
llamar herramientas para siempre. En esta máquina, con inferencia CPU-only,
cada iteración cuesta segundos reales — un loop descontrolado no es un bug
molesto, es el producto inutilizable.

El límite vive EN EL ESTADO y no en el código de cada nodo. Si cada nodo tuviera
que acordarse de chequear un contador, alcanzaría con que uno se olvide.
"""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from agent.state import AnalysisState, Intencion, PasoPlan, Periodo
from agent.tools.registry import ToolName


def _estado(**kw: Any) -> AnalysisState:
    base: dict[str, Any] = dict(
        request_id="req-001",
        consulta="Compará Producto A y Producto B en los últimos 30 días",
    )
    base.update(kw)
    return AnalysisState(**base)


# --- Construcción ------------------------------------------------------------

def test_el_estado_arranca_vacio_pero_valido() -> None:
    e = _estado()
    assert e.intencion is None
    assert e.plan == []
    assert e.resultados_tools == {}
    assert e.reintentos == 0
    assert e.llamadas_tools == 0
    assert e.trace_id  # se genera solo: sin él no hay trazabilidad


def test_la_consulta_no_puede_estar_vacia() -> None:
    with pytest.raises(ValidationError):
        AnalysisState(request_id="req-001", consulta="")


def test_el_periodo_rechaza_rangos_invertidos() -> None:
    with pytest.raises(ValidationError):
        Periodo(desde=date(2026, 3, 31), hasta=date(2026, 1, 1))


# --- Límite de llamadas a herramientas ---------------------------------------

def test_puede_llamar_tools_hasta_el_limite() -> None:
    e = _estado(max_llamadas_tools=3)
    for _ in range(3):
        assert e.puede_llamar_tool()
        e.registrar_llamada_tool()
    assert not e.puede_llamar_tool()


def test_exceder_el_limite_de_tools_deja_una_advertencia() -> None:
    """El límite no se alcanza en silencio.

    Si el agente se queda sin presupuesto de herramientas, el informe tiene que
    decirlo: puede estar incompleto, y el lector merece saberlo.
    """
    e = _estado(max_llamadas_tools=1)
    e.registrar_llamada_tool()
    e.registrar_llamada_tool()
    assert any("herramienta" in w.lower() for w in e.advertencias), e.advertencias


def test_el_contador_de_tools_no_se_puede_falsear() -> None:
    e = _estado(max_llamadas_tools=2)
    e.registrar_llamada_tool()
    assert e.llamadas_tools == 1


# --- Límite de reintentos ----------------------------------------------------

def test_puede_reintentar_hasta_el_maximo() -> None:
    e = _estado(max_reintentos=2)
    assert e.puede_reintentar()
    e.registrar_reintento()
    assert e.puede_reintentar()
    e.registrar_reintento()
    assert not e.puede_reintentar()


def test_agotar_reintentos_deja_una_advertencia() -> None:
    e = _estado(max_reintentos=1)
    e.registrar_reintento()
    e.registrar_reintento()
    assert any("replan" in w.lower() or "reintent" in w.lower()
               for w in e.advertencias), e.advertencias


# --- Plan --------------------------------------------------------------------

def test_el_plan_solo_admite_herramientas_conocidas() -> None:
    """Una tool inexistente en el plan es una alucinación del modelo.

    Se rechaza en el borde: si llegara a la ejecución, habría que manejar el
    error en cada nodo que despacha herramientas.
    """
    with pytest.raises(ValidationError):
        # El tool inválido es deliberado: es exactamente lo que se está
        # probando. El ignore evita que el verificador de tipos "arregle"
        # el test impidiendo escribir el caso que tiene que fallar.
        PasoPlan(tool="borrar_la_base", argumentos={}, razon="x")  # type: ignore[arg-type]


def test_un_paso_valido_se_construye() -> None:
    p = PasoPlan(tool=ToolName.PRODUCT_METRICS,
                 argumentos={"product_ids": ["P001"]},
                 razon="Necesito los KPIs para comparar")
    assert p.tool == "product_metrics"


# --- Registro de resultados --------------------------------------------------

def test_registrar_resultado_de_tool_lo_deja_disponible() -> None:
    e = _estado()
    e.registrar_resultado("product_metrics", {"P001": {"unidades": 100}})
    assert "product_metrics" in e.resultados_tools


def test_hay_evidencia_suficiente_requiere_resultados() -> None:
    """El EvidenceGate del grafo decide seguir o replanificar según esto.

    Sin resultados de herramientas no hay nada que sintetizar, y dejar que el
    modelo redacte igual es exactamente cómo se producen los informes inventados.
    """
    e = _estado()
    assert not e.hay_evidencia_suficiente()
    e.registrar_resultado("product_metrics", {"P001": {"unidades": 100}})
    assert e.hay_evidencia_suficiente()


def test_un_resultado_vacio_no_cuenta_como_evidencia() -> None:
    e = _estado()
    e.registrar_resultado("product_metrics", {})
    assert not e.hay_evidencia_suficiente()


# --- Intención ---------------------------------------------------------------

def test_la_intencion_solo_admite_valores_conocidos() -> None:
    with pytest.raises(ValidationError):
        _estado(intencion="lo_que_se_me_ocurra")


def test_fuera_de_alcance_es_una_intencion_valida() -> None:
    """Que el agente pueda decir "esto no me corresponde" es una capacidad, no
    una falla. Un agente que siempre intenta responder, siempre responde algo
    — aunque no tenga con qué."""
    e = _estado(intencion=Intencion.FUERA_DE_ALCANCE)
    assert e.intencion == Intencion.FUERA_DE_ALCANCE


# --- Trazabilidad ------------------------------------------------------------

def test_cada_paso_queda_registrado_en_el_trace() -> None:
    e = _estado()
    e.registrar_paso("router", 85)
    e.registrar_paso("sql_tool", 140, tool="product_metrics")
    assert [p.nodo for p in e.trace] == ["router", "sql_tool"]
    assert e.duracion_total_ms == 225


def test_el_trace_id_es_estable_durante_toda_la_ejecucion() -> None:
    e = _estado()
    inicial = e.trace_id
    e.registrar_paso("router", 10)
    e.registrar_llamada_tool()
    assert e.trace_id == inicial
