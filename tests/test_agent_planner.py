"""Tests del nodo PlanBuilder.

El planificador NO usa el modelo de lenguaje, y esa es la decisión de diseño
más importante del nodo.

Con la intención ya clasificada y las entidades ya extraídas, decidir qué
herramienta llamar es un `if`. Pedírselo al LLM costaría entre 12 y 41 segundos
por consulta, podría equivocarse, y no aportaría nada que el software no
resuelva mejor.

En la V1 hay una sola herramienta disponible (`product_metrics`). El plan
igual existe como estructura porque las fases 3 y 4 van a sumar RAG, research
público y ML, y entonces sí habrá algo que planificar. Lo que no va a cambiar es
quién decide: el software, sobre datos ya validados.
"""

from datetime import date
from typing import Any

import pytest

from agent.nodes.planner import planificar
from agent.state import AnalysisState, Intencion, Periodo


def _estado(intencion: Intencion | None = Intencion.PRODUCT_PERFORMANCE,
            entidades: list[str] | None = None,
            periodo: bool = True, **kw: Any) -> AnalysisState:
    base: dict[str, Any] = dict(
        request_id="req-001",
        consulta="Compará P001 y P002",
        intencion=intencion,
        entidades=entidades if entidades is not None else ["P001", "P002"],
    )
    if periodo:
        base["periodo"] = Periodo(desde=date(2026, 1, 1), hasta=date(2026, 3, 31))
    base.update(kw)
    return AnalysisState(**base)


# --- Plan según intención ----------------------------------------------------

def test_product_performance_planifica_consultar_metricas() -> None:
    estado = planificar(_estado())
    assert [p.tool for p in estado.plan] == ["product_metrics"]
    assert estado.plan[0].argumentos["product_ids"] == ["P001", "P002"]


def test_hybrid_tambien_consulta_metricas_en_la_v1() -> None:
    """La V1 no tiene RAG ni research público todavía.

    Una consulta híbrida se resuelve con lo que hay —las métricas internas— y
    el informe advierte que falta la parte externa. Es preferible a fallar: el
    usuario obtiene la mitad útil en vez de nada.
    """
    estado = planificar(_estado(intencion=Intencion.HYBRID))
    assert [p.tool for p in estado.plan] == ["product_metrics"]
    assert any("externo" in w.lower() or "contexto" in w.lower()
               for w in estado.advertencias), estado.advertencias


def test_company_research_sin_empresa_identificada_no_genera_plan() -> None:
    """El router puede clasificar company_research sin lograr extraer a
    quién investigar. Sin empresa no hay nada que ejecutar — cortar sigue
    siendo correcto, el motivo ya no dice "no implementado": ahora sí lo
    está, conectado a SEC EDGAR (ADR-014)."""
    estado = planificar(_estado(intencion=Intencion.COMPANY_RESEARCH,
                                entidades=[]))
    assert estado.plan == []
    assert any("identificar" in w.lower() and "empresa" in w.lower()
               for w in estado.advertencias), estado.advertencias


def test_company_research_con_empresa_planifica_research_company() -> None:
    """Con `estado.empresas` poblado (lo hace el router, no un regex — ver
    `agent/nodes/router.py`), el planner arma un PasoPlan real contra
    `research_company`."""
    estado = _estado(intencion=Intencion.COMPANY_RESEARCH, entidades=[])
    estado.empresas = ["Apple", "Microsoft"]
    resultado = planificar(estado)
    assert [p.tool for p in resultado.plan] == ["research_company"]
    assert resultado.plan[0].argumentos["nombres"] == ["Apple", "Microsoft"]


def test_company_research_limita_a_dos_empresas_por_consulta() -> None:
    """Mismo techo que `forecast_sales` aplica sobre entidades: auditable,
    no arbitrario."""
    estado = _estado(intencion=Intencion.COMPANY_RESEARCH, entidades=[])
    estado.empresas = ["Apple", "Microsoft", "Tesla"]
    resultado = planificar(estado)
    assert resultado.plan[0].argumentos["nombres"] == ["Apple", "Microsoft"]


def test_hybrid_con_empresa_planifica_ambas_tools() -> None:
    """Un híbrido con productos internos Y una empresa externa identificada
    termina con las dos tools en el plan — las dos ramas conviven."""
    estado = _estado(intencion=Intencion.HYBRID)
    estado.empresas = ["Tesla"]
    resultado = planificar(estado)
    assert {p.tool for p in resultado.plan} == {"product_metrics", "research_company"}


def test_fuera_de_alcance_no_genera_plan() -> None:
    estado = planificar(_estado(intencion=Intencion.FUERA_DE_ALCANCE,
                                entidades=[]))
    assert estado.plan == []


def test_sin_intencion_no_genera_plan() -> None:
    estado = _estado()
    estado.intencion = None
    assert planificar(estado).plan == []


# --- Validaciones ------------------------------------------------------------

def test_sin_periodo_usa_uno_por_defecto() -> None:
    """Un plan sin período no se puede ejecutar. Antes que fallar, se completa
    con el default y se advierte: el usuario obtiene un resultado y sabe sobre
    qué ventana se calculó."""
    estado = planificar(_estado(periodo=False))
    assert estado.periodo is not None
    assert estado.plan[0].argumentos["desde"] == estado.periodo.desde


def test_sin_entidades_no_planifica_metricas() -> None:
    estado = planificar(_estado(entidades=[]))
    assert estado.plan == []


def test_cada_paso_lleva_su_justificacion() -> None:
    """La razón se muestra en "Cómo se obtuvo" y permite auditar si el agente
    eligió bien la herramienta."""
    estado = planificar(_estado())
    assert all(p.razon for p in estado.plan)


def test_el_plan_respeta_el_presupuesto_de_herramientas() -> None:
    """Planificar más llamadas de las que el presupuesto admite es planificar
    un fracaso."""
    estado = _estado(max_llamadas_tools=0)
    planificar(estado)
    assert estado.plan == []
    assert any("presupuesto" in w.lower() or "límite" in w.lower()
               for w in estado.advertencias), estado.advertencias


def test_registra_el_paso_en_el_trace() -> None:
    estado = planificar(_estado())
    assert any(p.nodo == "planner" for p in estado.trace)


# --- Replanificación ---------------------------------------------------------

def test_replanificar_amplia_el_periodo() -> None:
    """Cuando el primer intento no encontró datos, la hipótesis más probable es
    que el período era demasiado corto. Ampliarlo es la corrección barata antes
    de darse por vencido.
    """
    estado = _estado()
    planificar(estado)
    assert estado.periodo is not None
    ancho_inicial = (estado.periodo.hasta - estado.periodo.desde).days

    estado.registrar_reintento()
    planificar(estado, replanificando=True)

    assert (estado.periodo.hasta - estado.periodo.desde).days > ancho_inicial


def test_replanificar_deja_constancia() -> None:
    estado = _estado()
    estado.registrar_reintento()
    planificar(estado, replanificando=True)
    assert any("amplió" in w.lower() or "amplio" in w.lower()
               for w in estado.advertencias), estado.advertencias


@pytest.mark.parametrize("intencion", list(Intencion))
def test_ninguna_intencion_hace_explotar_al_planificador(
    intencion: Intencion,
) -> None:
    estado = planificar(_estado(intencion=intencion))
    assert isinstance(estado.plan, list)
