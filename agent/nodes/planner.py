"""Nodo PlanBuilder: decide qué herramientas ejecutar.

**No usa el modelo de lenguaje**, y es deliberado.

Con la intención ya clasificada y las entidades ya extraídas, decidir qué
herramienta llamar es un `if`. Pedírselo al LLM costaría entre 12 y 41 segundos
por consulta en esta máquina, podría equivocarse, y no aportaría nada que el
software no resuelva mejor. Es la misma lección que dejó el diagnóstico del
router: cuando hay un LLM a mano, todo empieza a parecer clavo.

En la V1 hay una sola herramienta (`product_metrics`). El plan igual existe
como estructura porque las fases 3 y 4 van a sumar RAG, research público y ML —
ahí sí habrá algo que planificar. Lo que no va a cambiar es quién decide.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from agent.state import AnalysisState, Intencion, PasoPlan, Periodo

DIAS_POR_DEFECTO = 30
FACTOR_AMPLIACION = 3
MAX_DIAS = 366 * 3
HOY_POR_DEFECTO = date(2026, 6, 30)


def _asegurar_periodo(estado: AnalysisState) -> None:
    if estado.periodo is not None:
        return
    estado.periodo = Periodo(
        desde=HOY_POR_DEFECTO - timedelta(days=DIAS_POR_DEFECTO - 1),
        hasta=HOY_POR_DEFECTO,
    )
    estado._advertir(
        f"No se identificó un período en la consulta. Se usaron los últimos "
        f"{DIAS_POR_DEFECTO} días hasta {HOY_POR_DEFECTO.isoformat()}."
    )


def _ampliar_periodo(estado: AnalysisState) -> None:
    """Ensancha la ventana temporal para el segundo intento.

    Cuando el primer intento no encontró datos, la hipótesis más probable es que
    el período era demasiado corto —un producto que se vendió poco, un rango que
    cayó entre campañas—. Ampliarlo es la corrección barata antes de darse por
    vencido, y mucho más barata que volver a consultar al modelo.
    """
    if estado.periodo is None:
        _asegurar_periodo(estado)
        return

    dias = (estado.periodo.hasta - estado.periodo.desde).days + 1
    nuevos = min(dias * FACTOR_AMPLIACION, MAX_DIAS)
    estado.periodo = Periodo(
        desde=estado.periodo.hasta - timedelta(days=nuevos - 1),
        hasta=estado.periodo.hasta,
    )
    estado._advertir(
        f"No se encontraron datos suficientes en el período original. Se amplió "
        f"la ventana a {nuevos} días."
    )


def planificar(estado: AnalysisState, replanificando: bool = False) -> AnalysisState:
    """Construye el plan de ejecución a partir del estado ya interpretado."""
    inicio = time.perf_counter()
    estado.plan = []

    if replanificando:
        _ampliar_periodo(estado)

    if estado.intencion is None or estado.intencion == Intencion.FUERA_DE_ALCANCE:
        estado.registrar_paso("planner", int((time.perf_counter() - inicio) * 1000))
        return estado

    if not estado.puede_llamar_tool():
        estado._advertir(
            "No queda presupuesto de llamadas a herramientas: no se generó un "
            "plan. Planificar lo que no se puede ejecutar solo gasta tiempo."
        )
        estado.registrar_paso("planner", int((time.perf_counter() - inicio) * 1000))
        return estado

    if estado.intencion == Intencion.COMPANY_RESEARCH:
        # Sin acceso a fuentes públicas todavía. Inventar un plan que no se puede
        # cumplir solo gasta tiempo para llegar al mismo lugar.
        estado._advertir(
            "La investigación de empresas externas no está disponible en esta "
            "versión: requiere las fuentes públicas de la fase 3."
        )
        estado.registrar_paso("planner", int((time.perf_counter() - inicio) * 1000))
        return estado

    if estado.entidades:
        _asegurar_periodo(estado)
        estado.plan.append(PasoPlan(
            tool="product_metrics",
            argumentos={
                "product_ids": list(estado.entidades),
                "desde": estado.periodo.desde,
                "hasta": estado.periodo.hasta,
            },
            razon=(
                f"Obtener los KPIs de {', '.join(estado.entidades)} entre "
                f"{estado.periodo.desde} y {estado.periodo.hasta} para "
                "sustentar el análisis con datos reales."
            ),
        ))

    if estado.intencion == Intencion.HYBRID:
        # La consulta pedía contexto externo y todavía no se puede dar. Se
        # entrega la mitad útil y se advierte, en vez de fallar entero.
        estado._advertir(
            "La consulta pedía contexto externo de mercado, que esta versión "
            "todavía no puede recuperar. El análisis se limita a los datos "
            "internos."
        )

    estado.registrar_paso("planner", int((time.perf_counter() - inicio) * 1000))
    return estado
