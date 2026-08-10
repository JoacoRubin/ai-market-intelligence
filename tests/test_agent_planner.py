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

import pytest

from agent.nodes.planner import planificar
from agent.state import AnalysisState, Intencion, Periodo


def _estado(intencion=Intencion.PRODUCT_PERFORMANCE, entidades=None,
            periodo=True, **kw) -> AnalysisState:
    base = dict(
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

def test_product_performance_planifica_consultar_metricas():
    estado = planificar(_estado())
    assert [p.tool for p in estado.plan] == ["product_metrics"]
    assert estado.plan[0].argumentos["product_ids"] == ["P001", "P002"]


def test_hybrid_tambien_consulta_metricas_en_la_v1():
    """La V1 no tiene RAG ni research público todavía.

    Una consulta híbrida se resuelve con lo que hay —las métricas internas— y
    el informe advierte que falta la parte externa. Es preferible a fallar: el
    usuario obtiene la mitad útil en vez de nada.
    """
    estado = planificar(_estado(intencion=Intencion.HYBRID))
    assert [p.tool for p in estado.plan] == ["product_metrics"]
    assert any("externo" in w.lower() or "contexto" in w.lower()
               for w in estado.advertencias), estado.advertencias


def test_company_research_no_tiene_herramientas_en_la_v1():
    """Sin acceso a fuentes públicas todavía, no hay nada que ejecutar.

    El plan queda vacío y el grafo va a cortar en el EvidenceGate. Inventar un
    plan que no se puede cumplir solo gasta tiempo para llegar al mismo lugar.
    """
    estado = planificar(_estado(intencion=Intencion.COMPANY_RESEARCH,
                                entidades=[]))
    assert estado.plan == []
    assert any("no está disponible" in w.lower() or "fase" in w.lower()
               for w in estado.advertencias), estado.advertencias


def test_fuera_de_alcance_no_genera_plan():
    estado = planificar(_estado(intencion=Intencion.FUERA_DE_ALCANCE,
                                entidades=[]))
    assert estado.plan == []


def test_sin_intencion_no_genera_plan():
    estado = _estado()
    estado.intencion = None
    assert planificar(estado).plan == []


# --- Validaciones ------------------------------------------------------------

def test_sin_periodo_usa_uno_por_defecto():
    """Un plan sin período no se puede ejecutar. Antes que fallar, se completa
    con el default y se advierte: el usuario obtiene un resultado y sabe sobre
    qué ventana se calculó."""
    estado = planificar(_estado(periodo=False))
    assert estado.periodo is not None
    assert estado.plan[0].argumentos["desde"] == estado.periodo.desde


def test_sin_entidades_no_planifica_metricas():
    estado = planificar(_estado(entidades=[]))
    assert estado.plan == []


def test_cada_paso_lleva_su_justificacion():
    """La razón se muestra en "Cómo se obtuvo" y permite auditar si el agente
    eligió bien la herramienta."""
    estado = planificar(_estado())
    assert all(p.razon for p in estado.plan)


def test_el_plan_respeta_el_presupuesto_de_herramientas():
    """Planificar más llamadas de las que el presupuesto admite es planificar
    un fracaso."""
    estado = _estado(max_llamadas_tools=0)
    planificar(estado)
    assert estado.plan == []
    assert any("presupuesto" in w.lower() or "límite" in w.lower()
               for w in estado.advertencias), estado.advertencias


def test_registra_el_paso_en_el_trace():
    estado = planificar(_estado())
    assert any(p.nodo == "planner" for p in estado.trace)


# --- Replanificación ---------------------------------------------------------

def test_replanificar_amplia_el_periodo():
    """Cuando el primer intento no encontró datos, la hipótesis más probable es
    que el período era demasiado corto. Ampliarlo es la corrección barata antes
    de darse por vencido.
    """
    estado = _estado()
    planificar(estado)
    ancho_inicial = (estado.periodo.hasta - estado.periodo.desde).days

    estado.registrar_reintento()
    planificar(estado, replanificando=True)

    assert (estado.periodo.hasta - estado.periodo.desde).days > ancho_inicial


def test_replanificar_deja_constancia():
    estado = _estado()
    estado.registrar_reintento()
    planificar(estado, replanificando=True)
    assert any("amplió" in w.lower() or "amplio" in w.lower()
               for w in estado.advertencias), estado.advertencias


@pytest.mark.parametrize("intencion", list(Intencion))
def test_ninguna_intencion_hace_explotar_al_planificador(intencion):
    estado = planificar(_estado(intencion=intencion))
    assert isinstance(estado.plan, list)
