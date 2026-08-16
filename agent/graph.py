"""El grafo de análisis.

    START
      ↓ router          (LLM + corrección determinística)
      ↓ planner         (determinístico)
      ↓ ejecutor        (SQL parametrizado)
      ↓ EvidenceGate    ── ¿alcanza? ── No ──→ planner (máx. 2 replanificaciones)
      ↓ Sí
      ↓ synthesizer     (LLM, con respaldo determinístico)
      ↓ validator       (determinístico)
    FIN

De seis etapas, **una sola** usa el modelo para decidir algo. Las demás son
software clásico, porque en cada uno de esos casos el software es más rápido,
más barato y no se equivoca.

El cliente del modelo se inyecta al construir el grafo. Eso permite ejercitarlo
entero —ramas, límites, replanificación, degradación— con un doble
determinístico, en milisegundos. Con llamadas reales de 12 a 41 segundos, una
suite que invocara el modelo sería una suite que nadie corre.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.llm import ClienteLLM
from agent.nodes.ejecutor import ejecutar_plan
from agent.nodes.planner import planificar
from agent.nodes.router import HOY_POR_DEFECTO, enrutar
from agent.nodes.synthesizer import sintetizar
from agent.nodes.validator import validar_informe
from agent.state import AnalysisState, Intencion


def construir_grafo(
    cliente: ClienteLLM,
    hoy: date = HOY_POR_DEFECTO,
    ahora: datetime | None = None,
    indice: Any = None,
    con_ml: bool = True,
) -> CompiledStateGraph[AnalysisState]:
    """Arma el grafo con el cliente de modelo ya inyectado."""

    # El parámetro de los nodos se llama `state` y no `estado`, que es la única
    # excepción al castellano en este archivo. No es un capricho: LangGraph
    # define los nodos con un Protocol cuyo parámetro se llama `state` y admite
    # ser pasado por nombre. Con otro nombre, el código queda dependiendo de que
    # LangGraph invoque siempre posicionalmente — hoy lo hace, pero es un
    # detalle interno que puede cambiar sin aviso en una versión menor.
    # Respetar el nombre es implementar el protocolo; no hacerlo es apostar.

    def nodo_router(state: AnalysisState) -> AnalysisState:
        return enrutar(state, cliente, hoy=hoy)

    def nodo_planner(state: AnalysisState) -> AnalysisState:
        """Arma el plan. Si ya hubo una ejecución, esto es una replanificación.

        El contador de reintentos se incrementa ACÁ y no en la arista
        condicional. Las funciones de las aristas solo eligen la rama: LangGraph
        descarta las mutaciones que hagan sobre el estado. Incrementar el
        contador ahí dejaba `puede_reintentar()` en True para siempre y el grafo
        giraba en círculos — el límite que creíamos tener no existía.
        """
        replan = state.ya_ejecutado
        if replan:
            state.registrar_reintento()
        return planificar(state, replanificando=replan,
                          con_rag=indice is not None, con_ml=con_ml)

    def nodo_ejecutor(state: AnalysisState) -> AnalysisState:
        return ejecutar_plan(state, indice=indice)

    def nodo_synthesizer(state: AnalysisState) -> AnalysisState:
        return sintetizar(state, cliente, ahora=ahora)

    def nodo_validator(state: AnalysisState) -> AnalysisState:
        if state.informe is None:
            return state
        resultado = validar_informe(state.informe, state.resultados_tools,
                                    state.evidencia)
        state.informe = resultado.informe
        state.advertencias = list(state.informe.advertencias)
        return state

    # --- ramas condicionales ---

    def hay_algo_que_analizar(estado: AnalysisState) -> str:
        """Corta temprano lo que no se puede analizar.

        Seguir con una consulta fuera de alcance solo gasta tiempo de CPU para
        llegar a un informe vacío.
        """
        if estado.intencion in (None, Intencion.FUERA_DE_ALCANCE):
            return "fin"
        return "planificar"

    def evidencia_suficiente(estado: AnalysisState) -> str:
        """El EvidenceGate: seguir, replanificar o rendirse.

        Es una función PURA: solo mira el estado y elige la rama. No lo modifica,
        porque LangGraph descarta las mutaciones hechas en las aristas
        condicionales — y una mutación descartada en silencio es un límite que
        parece existir y no existe.

        Rendirse es una salida legítima. Un agente que insiste indefinidamente no
        es más capaz: es más caro, y en CPU cada vuelta cuesta segundos reales.
        """
        if estado.hay_evidencia_suficiente():
            return "sintetizar"
        if estado.puede_reintentar():
            return "replanificar"
        # Agotados los intentos, el sintetizador deja constancia de que no hubo
        # datos. Cortar en seco dejaría al usuario sin ninguna explicación.
        return "sintetizar"

    grafo = StateGraph(AnalysisState)
    grafo.add_node("router", nodo_router)
    grafo.add_node("planner", nodo_planner)
    grafo.add_node("ejecutor", nodo_ejecutor)
    grafo.add_node("synthesizer", nodo_synthesizer)
    grafo.add_node("validator", nodo_validator)

    grafo.add_edge(START, "router")
    grafo.add_conditional_edges(
        "router", hay_algo_que_analizar,
        {"planificar": "planner", "fin": END},
    )
    grafo.add_edge("planner", "ejecutor")
    grafo.add_conditional_edges(
        "ejecutor", evidencia_suficiente,
        {"sintetizar": "synthesizer", "replanificar": "planner"},
    )
    grafo.add_edge("synthesizer", "validator")
    grafo.add_edge("validator", END)

    return grafo.compile()


def ejecutar(
    estado: AnalysisState,
    cliente: ClienteLLM,
    hoy: date = HOY_POR_DEFECTO,
    ahora: datetime | None = None,
    indice: Any = None,
) -> AnalysisState:
    """Corre el grafo sobre un estado ya construido.

    Permite entrar con la interpretación resuelta —cuando la solicitud llegó
    estructurada— y que el router se saltee solo.
    """
    grafo = construir_grafo(cliente, hoy=hoy, ahora=ahora, indice=indice)
    resultado = grafo.invoke(estado)
    # LangGraph puede devolver un dict con el estado; se normaliza a modelo.
    return (resultado if isinstance(resultado, AnalysisState)
            else AnalysisState(**resultado))


def analizar(
    consulta: str,
    cliente: ClienteLLM,
    request_id: str = "req-local",
    hoy: date = HOY_POR_DEFECTO,
    ahora: datetime | None = None,
    indice: Any = None,
) -> AnalysisState:
    """Ejecuta el análisis desde una consulta en lenguaje natural."""
    return ejecutar(
        AnalysisState(request_id=request_id, consulta=consulta),
        cliente, hoy=hoy, ahora=ahora, indice=indice,
    )
