"""Caso de uso que ejecuta el grafo y persiste su ciclo de vida."""

from __future__ import annotations

from agent.graph import ejecutar as ejecutar_grafo
from agent.llm import ClienteLLM
from agent.state import AnalysisState, Intencion, Periodo
from application.models import Analisis, EstadoAnalisis
from application.ports import Almacen
from rag.build import cargar_indice


def estado_inicial(registro: Analisis) -> AnalysisState:
    """Construye el estado del grafo según la forma de la solicitud."""
    estado = AnalysisState(request_id=registro.id, consulta=registro.consulta)

    if registro.product_ids and registro.desde and registro.hasta:
        estado.intencion = Intencion.PRODUCT_PERFORMANCE
        estado.entidades = list(registro.product_ids)
        estado.periodo = Periodo(desde=registro.desde, hasta=registro.hasta)

    return estado


def _reclamar(
    analysis_id: str,
    almacen: Almacen,
    *,
    permitir_reintento: bool,
) -> Analisis | None:
    registro = almacen.transicionar(
        analysis_id,
        desde=EstadoAnalisis.PENDIENTE,
        hacia=EstadoAnalisis.PROCESANDO,
    )
    if registro is None and permitir_reintento:
        registro = almacen.transicionar(
            analysis_id,
            desde=EstadoAnalisis.FALLIDO,
            hacia=EstadoAnalisis.PROCESANDO,
            cambios={"error": None},
        )
    return registro


def procesar_analisis(
    analysis_id: str,
    cliente: ClienteLLM,
    almacen: Almacen,
    *,
    propagar_error: bool = False,
) -> None:
    """Ejecuta el análisis con CAS; RQ puede optar por propagar el error.

    BackgroundTasks conserva la degradación histórica: el fallo queda en el
    recurso sin escapar al servidor HTTP. El entrypoint de RQ usa
    ``propagar_error=True`` para que RQ marque el job como fallido y aplique
    sus reintentos.
    """
    registro = _reclamar(
        analysis_id, almacen, permitir_reintento=propagar_error
    )
    if registro is None:
        # No existe, fue cancelado, ya terminó o otro worker lo reclamó.
        return

    try:
        estado = ejecutar_grafo(
            estado_inicial(registro), cliente, indice=cargar_indice()
        )
    except Exception as error:
        almacen.transicionar(
            analysis_id,
            desde=EstadoAnalisis.PROCESANDO,
            hacia=EstadoAnalisis.FALLIDO,
            version_esperada=registro.version,
            cambios={"error": f"{type(error).__name__}: {error}"},
        )
        if propagar_error:
            raise
        return

    cambios: dict[str, object] = {
        "informe": estado.informe,
        "intencion": estado.intencion.value if estado.intencion else None,
        "product_ids": list(estado.entidades),
        "etapas": [paso.nodo for paso in estado.trace],
        "advertencias": list(estado.advertencias),
        "error": None,
    }
    if estado.periodo:
        cambios["desde"] = estado.periodo.desde
        cambios["hasta"] = estado.periodo.hasta

    # Si DELETE ganó la carrera, la versión/estado ya no coinciden y el CAS
    # devuelve None: el resultado tardío se descarta, nunca resucita el recurso.
    almacen.transicionar(
        analysis_id,
        desde=EstadoAnalisis.PROCESANDO,
        hacia=EstadoAnalisis.COMPLETADO,
        version_esperada=registro.version,
        cambios=cambios,
    )

