"""Qué pasa cuando algo falla a mitad de un análisis.

El sistema promete degradación en todas sus capas: sin índice FAISS el agente
analiza sin evidencia documental, sin LLM arma el informe determinístico, sin
Redis corre en proceso. Estos tests verifican que esa promesa también valga
cuando **una herramienta revienta**, que hasta esta versión no era cierto.

La asimetría era medible. `research_company` manejaba sus propias excepciones
porque sale a internet y ahí fallar es lo normal; `product_metrics`,
`search_documents` y `forecast_sales` no tenían ni un `except`. El ejecutor
protegía la validación de argumentos pero no la ejecución, así que un
`pyodbc.Error` —SQL Server que se cae a mitad de una corrida— subía hasta el
borde del grafo y el análisis entero terminaba FALLIDO, tirando el trabajo ya
hecho por los pasos anteriores.

La garantía vive en el ejecutor y no en cada tool a propósito: si dependiera de
que cada tool se acuerde, alcanzaría con que una se olvide — y tres se habían
olvidado.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel

from agent.graph import construir_grafo, ejecutar
from agent.llm import ClientePredecible
from agent.nodes import ejecutor as modulo_ejecutor
from agent.nodes.ejecutor import ejecutar_plan
from agent.state import AnalysisState, Intencion, PasoPlan, Periodo
from agent.tools.registry import DefinicionTool, ToolName
from core.report import MetricaProducto


class EntradaCualquiera(BaseModel):
    """Esquema mínimo: estos tests miran el manejo del fallo, no los argumentos."""


def _tool_que_explota(excepcion: Exception) -> DefinicionTool:
    def handler(_e: BaseModel, _s: AnalysisState, _c: Any) -> Any:
        raise excepcion

    return DefinicionTool(
        name=ToolName.PRODUCT_METRICS,
        input_model=EntradaCualquiera,
        handler=handler,
    )


def _tool_que_funciona(resultado: Any) -> DefinicionTool:
    def handler(_e: BaseModel, estado: AnalysisState, _c: Any) -> Any:
        estado.resultados_tools[ToolName.SEARCH_DOCUMENTS] = resultado
        return resultado

    return DefinicionTool(
        name=ToolName.SEARCH_DOCUMENTS,
        input_model=EntradaCualquiera,
        handler=handler,
    )


def _estado_con_plan(*tools: ToolName) -> AnalysisState:
    estado = AnalysisState(request_id="req-test", consulta="Comparar P001 y P002")
    estado.plan = [PasoPlan(tool=t, razon="test") for t in tools]
    return estado


# --- el ejecutor sobrevive a una tool que revienta ---------------------------

@pytest.mark.parametrize("excepcion", [
    # El caso real que motivó el cambio: la base se cae a mitad del análisis.
    ConnectionError("SQL Server no responde"),
    # Un bug adentro de la tool tampoco puede tumbar el grafo.
    ValueError("los datos no alcanzan para el modelo"),
    RuntimeError("faiss: índice corrupto"),
    ZeroDivisionError("division by zero"),
])
def test_una_tool_que_revienta_no_tumba_el_ejecutor(
    excepcion: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool",
        lambda _n: _tool_que_explota(excepcion),
    )
    estado = _estado_con_plan(ToolName.PRODUCT_METRICS)

    resultado = ejecutar_plan(estado)

    assert resultado is estado


def test_el_fallo_queda_escrito_en_las_advertencias(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un fallo silencioso es peor que un fallo.

    El informe se entrega igual, así que quien lo lee tiene que poder saber que
    una parte no se pudo calcular — si no, un informe incompleto se ve idéntico
    a uno completo.
    """
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool",
        lambda _n: _tool_que_explota(ConnectionError("SQL Server no responde")),
    )
    estado = _estado_con_plan(ToolName.PRODUCT_METRICS)

    ejecutar_plan(estado)

    assert len(estado.advertencias) == 1
    advertencia = estado.advertencias[0]
    assert "product_metrics" in advertencia
    # El TIPO además del mensaje: "ConnectionError" dice "la infraestructura no
    # estaba", "ValueError" dice "los datos no daban". Son diagnósticos
    # distintos y el que lee el informe después necesita distinguirlos.
    assert "ConnectionError" in advertencia
    assert "SQL Server no responde" in advertencia


def test_el_fallo_queda_en_la_traza_con_su_duracion(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La traza es lo que hace auditable el informe: un paso que falló también
    es información, y omitirlo deja un hueco sin explicar en el replay."""
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool",
        lambda _n: _tool_que_explota(ValueError("x")),
    )
    estado = _estado_con_plan(ToolName.PRODUCT_METRICS)

    ejecutar_plan(estado)

    pasos = [p for p in estado.trace if p.nodo.endswith("_fallida")]
    assert len(pasos) == 1
    assert pasos[0].tool == ToolName.PRODUCT_METRICS
    assert pasos[0].duracion_ms >= 0


def test_un_paso_que_falla_no_se_lleva_puestos_a_los_demas(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """El punto entero del cambio.

    Antes, la primera tool que reventaba abortaba el plan completo y el análisis
    terminaba sin nada. Ahora el resto del plan se ejecuta y el informe sale con
    la evidencia que sí se pudo reunir.
    """
    definiciones = {
        ToolName.PRODUCT_METRICS: _tool_que_explota(ConnectionError("caída")),
        ToolName.SEARCH_DOCUMENTS: _tool_que_funciona([{"doc_id": "doc_1"}]),
    }
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool", lambda n: definiciones[ToolName(n)]
    )
    estado = _estado_con_plan(ToolName.PRODUCT_METRICS, ToolName.SEARCH_DOCUMENTS)

    ejecutar_plan(estado)

    assert estado.resultados_tools[ToolName.SEARCH_DOCUMENTS] == [{"doc_id": "doc_1"}]
    assert estado.hay_evidencia_suficiente() is True


def test_si_todas_las_tools_fallan_no_hay_evidencia_suficiente(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degradar no es inventar.

    Sobrevivir al fallo NO puede significar que el sintetizador redacte igual
    sobre la nada: el EvidenceGate tiene que seguir viendo que no hay con qué.
    Es la diferencia entre un informe incompleto y un informe inventado.
    """
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool",
        lambda _n: _tool_que_explota(ConnectionError("caída")),
    )
    estado = _estado_con_plan(ToolName.PRODUCT_METRICS)

    ejecutar_plan(estado)

    assert estado.hay_evidencia_suficiente() is False


# --- el grafo entero sobrevive -----------------------------------------------

def test_el_grafo_completo_termina_aunque_la_tool_reviente(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La prueba de extremo a extremo: entra una consulta, sale un estado.

    Sin el `try` del ejecutor esto levanta `ConnectionError` y el análisis queda
    FALLIDO. Con él, el grafo llega al final y el informe explica qué faltó.
    """
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool",
        lambda _n: _tool_que_explota(ConnectionError("SQL Server no responde")),
    )
    grafo = construir_grafo(ClientePredecible(), hoy=date(2026, 6, 30), con_ml=False)

    crudo = grafo.invoke(
        AnalysisState(request_id="req-e2e", consulta="Comparar P001 y P002")
    )
    final = crudo if isinstance(crudo, AnalysisState) else AnalysisState(**crudo)

    assert any("product_metrics" in a for a in final.advertencias)


def test_una_tool_sana_sigue_registrando_su_resultado(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guardián del camino feliz: el `try` no cambia lo que ya funcionaba."""
    resultado = [{"doc_id": "doc_7", "texto": "el proveedor reportó defectos"}]
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool", lambda _n: _tool_que_funciona(resultado)
    )
    estado = _estado_con_plan(ToolName.SEARCH_DOCUMENTS)

    ejecutar_plan(estado)

    assert estado.resultados_tools[ToolName.SEARCH_DOCUMENTS] == resultado
    assert estado.advertencias == []
    assert not [p for p in estado.trace if p.nodo.endswith("_fallida")]


# --- el gate distingue "no encontró" de "reventó" ----------------------------

def test_una_tool_que_revienta_queda_registrada_como_fallida(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool",
        lambda _n: _tool_que_explota(ConnectionError("caída")),
    )
    estado = _estado_con_plan(ToolName.PRODUCT_METRICS)

    ejecutar_plan(estado)

    assert estado.tools_fallidas == [ToolName.PRODUCT_METRICS]


def test_una_tool_que_devuelve_vacio_no_cuenta_como_fallida(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La distinción entera del cambio.

    Devolver vacío es un resultado: con otro período o otro producto puede
    aparecer algo, así que replanificar tiene sentido. Reventar no es un
    resultado.
    """
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool", lambda _n: _tool_que_funciona([])
    )
    estado = _estado_con_plan(ToolName.SEARCH_DOCUMENTS)

    ejecutar_plan(estado)

    assert estado.tools_fallidas == []
    assert estado.replanificar_puede_ayudar() is True


def test_si_todo_el_plan_fallo_replanificar_no_ayuda(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool",
        lambda _n: _tool_que_explota(ConnectionError("caída")),
    )
    estado = _estado_con_plan(ToolName.PRODUCT_METRICS)

    ejecutar_plan(estado)

    assert estado.replanificar_puede_ayudar() is False


def test_con_un_fallo_parcial_replanificar_sigue_teniendo_sentido(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterio conservador: solo se corta si TODO el plan falló.

    Con un fallo parcial, el replan puede conseguir lo que falta — cortar ahí
    sería perder un intento legítimo.
    """
    definiciones = {
        ToolName.PRODUCT_METRICS: _tool_que_explota(ConnectionError("caída")),
        ToolName.SEARCH_DOCUMENTS: _tool_que_funciona([]),
    }
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool", lambda n: definiciones[ToolName(n)]
    )
    estado = _estado_con_plan(ToolName.PRODUCT_METRICS, ToolName.SEARCH_DOCUMENTS)

    ejecutar_plan(estado)

    assert estado.replanificar_puede_ayudar() is True


def test_sin_plan_replanificar_siempre_puede_ayudar() -> None:
    """Sin plan no hay nada que declarar imposible."""
    assert AnalysisState(
        request_id="r", consulta="x"
    ).replanificar_puede_ayudar() is True


def test_el_grafo_no_gasta_reintentos_si_la_base_esta_caida(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """El ahorro concreto.

    Antes el grafo replanificaba dos veces contra una base caída para llegar
    exactamente al mismo informe. Cada vuelta son segundos reales de planner y
    ejecutor en CPU, y ninguna podía cambiar el resultado.
    """
    monkeypatch.setattr(
        modulo_ejecutor, "buscar_tool",
        lambda _n: _tool_que_explota(ConnectionError("SQL Server no responde")),
    )
    grafo = construir_grafo(ClientePredecible(), hoy=date(2026, 6, 30), con_ml=False)

    crudo = grafo.invoke(
        AnalysisState(request_id="req-sin-base", consulta="Comparar P001 y P002")
    )
    final = crudo if isinstance(crudo, AnalysisState) else AnalysisState(**crudo)

    assert final.reintentos == 0


# --- trazabilidad del validador ----------------------------------------------

def test_el_grafo_persiste_la_groundedness_que_midio_el_validador(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """`nodo_validator` calculaba la métrica y se quedaba solo con el informe.

    Sin persistirla, la única medida de cuánto de lo entregado está respaldado
    por las herramientas no llegaba a ningún lado: el eval y el replay tenían
    el texto de las advertencias —que dice QUÉ se descartó— pero no la
    proporción.

    Necesita datos reales y no una tool que revienta: sin ninguna métrica el
    sintetizador deja `informe=None` a propósito (no hay de qué escribir) y el
    validador ni corre — comportamiento correcto y anterior a este cambio, no
    lo que se está probando acá.
    """
    metrica = MetricaProducto(
        product_id="P001", nombre="Vertex calzado", unidades=120,
        revenue=45000.0, fuente="sql:product_metrics",
    )

    def handler(_e: BaseModel, estado: AnalysisState, _c: Any) -> Any:
        estado.resultados_tools[ToolName.PRODUCT_METRICS] = {"P001": metrica}

    definicion = DefinicionTool(
        name=ToolName.PRODUCT_METRICS, input_model=EntradaCualquiera, handler=handler,
    )
    monkeypatch.setattr(modulo_ejecutor, "buscar_tool", lambda _n: definicion)

    # Estado ya interpretado: el router (con ClientePredecible) lo respeta y no
    # lo pisa, así que el plan se arma directo con la tool que se interceptó.
    estado_inicial = AnalysisState(
        request_id="req-g", consulta="Comparar P001",
        intencion=Intencion.PRODUCT_PERFORMANCE, entidades=["P001"],
        periodo=Periodo(desde=date(2026, 1, 1), hasta=date(2026, 3, 31)),
    )
    final = ejecutar(
        estado_inicial, ClientePredecible(), hoy=date(2026, 6, 30),
    )

    assert final.informe is not None
    assert final.groundedness is not None
    assert 0.0 <= final.groundedness <= 1.0


def test_sin_validador_la_groundedness_es_none_y_no_cero() -> None:
    """"No se midió" y "se midió y dio pésimo" no pueden verse igual."""
    assert AnalysisState(request_id="r", consulta="x").groundedness is None
