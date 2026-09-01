"""Contrato del catálogo único de herramientas del agente.

El catálogo evita que estado, planner y ejecutor mantengan listas paralelas:
una herramienta activa tiene un nombre tipado, un esquema de entrada, una
regla de disponibilidad y un único handler de ejecución.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from agent.nodes import ejecutor
from agent.nodes.planner import planificar
from agent.state import AnalysisState, Intencion, PasoPlan, Periodo
from agent.tools.registry import (
    DefinicionTool,
    ToolName,
    catalogo_tools,
)


def _estado(**kw: Any) -> AnalysisState:
    base: dict[str, Any] = {
        "request_id": "req-registry",
        "consulta": "Proyectá las ventas de P001",
        "intencion": Intencion.PRODUCT_PERFORMANCE,
        "entidades": ["P001"],
        "periodo": Periodo(desde=date(2026, 1, 1), hasta=date(2026, 3, 31)),
    }
    base.update(kw)
    return AnalysisState(**base)


def test_el_catalogo_declara_exactamente_las_tools_implementadas() -> None:
    catalogo = catalogo_tools()

    assert set(catalogo) == set(ToolName)
    assert {nombre.value for nombre in catalogo} == {
        "product_metrics",
        "search_documents",
        "forecast_sales",
        "research_company",
    }
    assert all(definicion.name == nombre for nombre, definicion in catalogo.items())


def test_cada_tool_tiene_contrato_tipado_handler_y_disponibilidad() -> None:
    for definicion in catalogo_tools().values():
        assert issubclass(definicion.input_model, BaseModel)
        assert callable(definicion.handler)
        assert callable(definicion.availability)


def test_public_research_y_calculator_ya_no_son_capacidades_activas() -> None:
    for nombre in ("public_research", "calculator"):
        with pytest.raises(ValidationError):
            # El str invalido es deliberado: es exactamente lo que el
            # catalogo tiene que rechazar. El tipo mal puesto es el caso.
            PasoPlan(tool=nombre, argumentos={}, razon="no está implementada")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("con_rag", "con_ml"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_el_planner_solo_emite_tools_del_catalogo(
    con_rag: bool,
    con_ml: bool,
) -> None:
    estado = planificar(_estado(), con_rag=con_rag, con_ml=con_ml)

    assert {paso.tool for paso in estado.plan} <= set(catalogo_tools())


def test_search_documents_declara_su_dependencia_del_indice() -> None:
    definicion = catalogo_tools()[ToolName.SEARCH_DOCUMENTS]

    assert not definicion.esta_disponible(None)
    assert definicion.esta_disponible(object())


def test_el_ejecutor_delega_en_el_handler_del_catalogo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llamadas: list[tuple[int, AnalysisState, object]] = []
    contexto = object()

    class EntradaFalsa(BaseModel):
        valor: int

    def handler(
        entrada: BaseModel,
        estado: AnalysisState,
        contexto_recibido: Any,
    ) -> None:
        assert isinstance(entrada, EntradaFalsa)
        llamadas.append((entrada.valor, estado, contexto_recibido))

    definicion = DefinicionTool(
        name=ToolName.PRODUCT_METRICS,
        input_model=EntradaFalsa,
        handler=handler,
    )
    monkeypatch.setattr(ejecutor, "buscar_tool", lambda _nombre: definicion)
    estado = _estado(
        plan=[PasoPlan(
            tool=ToolName.PRODUCT_METRICS,
            argumentos={"valor": 7},
            razon="probar el despacho",
        )],
    )

    ejecutor.ejecutar_plan(estado, indice=contexto)

    assert llamadas == [(7, estado, contexto)]

