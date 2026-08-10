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

from langgraph.graph import END, START, StateGraph

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
):
    """Arma el grafo con el cliente de modelo ya inyectado."""

    def nodo_router(estado: AnalysisState) -> AnalysisState:
        return enrutar(estado, cliente, hoy=hoy)

    def nodo_planner(estado: AnalysisState) -> AnalysisState:
        """Arma el plan. Si ya hubo una ejecución, esto es una replanificación.

        El contador de reintentos se incrementa ACÁ y no en la arista
        condicional. Las funciones de las aristas solo eligen la rama: LangGraph
        descarta las mutaciones que hagan sobre el estado. Incrementar el
        contador ahí dejaba `puede_reintentar()` en True para siempre y el grafo
        giraba en círculos — el límite que creíamos tener no existía.
        """
        replan = estado.ya_ejecutado
        if replan:
            estado.registrar_reintento()
        return planificar(estado, replanificando=replan)

    def nodo_ejecutor(estado: AnalysisState) -> AnalysisState:
        return ejecutar_plan(estado)

    def nodo_synthesizer(estado: AnalysisState) -> AnalysisState:
        return sintetizar(estado, cliente, ahora=ahora)

    def nodo_validator(estado: AnalysisState) -> AnalysisState:
        if estado.informe is None:
            return estado
        resultado = validar_informe(estado.informe, estado.resultados_tools)
        estado.informe = resultado.informe
        estado.advertencias = list(estado.informe.advertencias)
        return estado

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
) -> AnalysisState:
    """Corre el grafo sobre un estado ya construido.

    Permite entrar con la interpretación resuelta —cuando la solicitud llegó
    estructurada— y que el router se saltee solo.
    """
    grafo = construir_grafo(cliente, hoy=hoy, ahora=ahora)
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
) -> AnalysisState:
    """Ejecuta el análisis desde una consulta en lenguaje natural."""
    return ejecutar(
        AnalysisState(request_id=request_id, consulta=consulta),
        cliente, hoy=hoy, ahora=ahora,
    )
