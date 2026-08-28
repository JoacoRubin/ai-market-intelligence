"""Nodo PlanBuilder: decide qué herramientas ejecutar.

**No usa el modelo de lenguaje**, y es deliberado.

Con la intención ya clasificada y las entidades ya extraídas, decidir qué
herramienta llamar es un `if`. Pedírselo al LLM costaría entre 12 y 41 segundos
por consulta en esta máquina, podría equivocarse, y no aportaría nada que el
software no resuelva mejor. Es la misma lección que dejó el diagnóstico del
router: cuando hay un LLM a mano, todo empieza a parecer clavo.

El catálogo activo tiene tres herramientas: métricas, evidencia documental y
forecast. El planificador solo referencia esos nombres tipados; una capacidad
futura no entra al plan hasta tener implementación y contrato ejecutable.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from agent.state import AnalysisState, Intencion, PasoPlan, Periodo
from agent.tools.registry import ToolName

DIAS_POR_DEFECTO = 30

# Palabras que indican que la consulta pide una proyección. Se detecta con
# software y no con el LLM: es una búsqueda de términos, y pedírsela al modelo
# costaría decenas de segundos para responder lo mismo.
PIDE_PROYECCION = (
    "proyect", "pronost", "pronóst", "forecast", "predic", "estim",
    "próximo mes", "proximo mes", "que va a vender", "qué va a vender",
    "a futuro", "próximos", "proximos",
)
FACTOR_AMPLIACION = 3
MAX_DIAS = 366 * 3
HOY_POR_DEFECTO = date(2026, 6, 30)


def _asegurar_periodo(estado: AnalysisState) -> Periodo:
    """Garantiza que el estado tenga período y **lo devuelve**.

    Devolverlo no es un detalle de estilo: una función que asegura algo pero no
    entrega nada obliga a quien la llama a volver a leer el atributo, que sigue
    siendo opcional. La garantía queda en el comentario en vez de en el tipo, y
    un comentario no se verifica.
    """
    if estado.periodo is not None:
        return estado.periodo
    periodo = Periodo(
        desde=HOY_POR_DEFECTO - timedelta(days=DIAS_POR_DEFECTO - 1),
        hasta=HOY_POR_DEFECTO,
    )
    estado.periodo = periodo
    estado._advertir(
        f"No se identificó un período en la consulta. Se usaron los últimos "
        f"{DIAS_POR_DEFECTO} días hasta {HOY_POR_DEFECTO.isoformat()}."
    )
    return periodo


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


def _pide_proyeccion(consulta: str) -> bool:
    texto = consulta.lower()
    return any(t in texto for t in PIDE_PROYECCION)


def planificar(
    estado: AnalysisState,
    replanificando: bool = False,
    con_rag: bool = False,
    con_ml: bool = False,
) -> AnalysisState:
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
        periodo = _asegurar_periodo(estado)
        estado.plan.append(PasoPlan(
            tool=ToolName.PRODUCT_METRICS,
            argumentos={
                "product_ids": list(estado.entidades),
                "desde": periodo.desde,
                "hasta": periodo.hasta,
            },
            razon=(
                f"Obtener los KPIs de {', '.join(estado.entidades)} entre "
                f"{periodo.desde} y {periodo.hasta} para "
                "sustentar el análisis con datos reales."
            ),
        ))

    # Evidencia documental: se busca SIEMPRE que haya productos, no solo en
    # consultas híbridas. Cuesta milisegundos —no interviene el LLM— y es lo
    # único que puede explicar POR QUÉ pasó lo que los números muestran.
    if con_rag and estado.entidades and estado.puede_llamar_tool():
        estado.plan.append(PasoPlan(
            tool=ToolName.SEARCH_DOCUMENTS,
            argumentos={
                "consulta": estado.consulta,
                "product_id": estado.entidades[0],
                "top_k": 4,
            },
            razon=(
                "Buscar evidencia documental que explique el comportamiento "
                f"observado en {estado.entidades[0]}."
            ),
        ))

    # El forecast solo se calcula si la consulta lo pide. Entrenar un modelo
    # y correr un backtest para una consulta que solo quería ver los KPIs es
    # trabajo que nadie pidió y latencia que el usuario paga.
    if con_ml and estado.entidades and _pide_proyeccion(estado.consulta):
        for pid in estado.entidades[:2]:
            if not estado.puede_llamar_tool():
                break
            estado.plan.append(PasoPlan(
                tool=ToolName.FORECAST_SALES,
                argumentos={"product_id": pid, "horizonte_dias": 30},
                razon=(
                    f"La consulta pide una proyección: estimar la demanda de "
                    f"{pid} a 30 días con su error medido por backtesting."
                ),
            ))

    if estado.intencion == Intencion.HYBRID and not con_rag:
        estado._advertir(
            "La consulta pedía contexto externo y la búsqueda documental no "
            "está disponible. El análisis se limita a los datos internos."
        )

    estado.registrar_paso("planner", int((time.perf_counter() - inicio) * 1000))
    return estado
