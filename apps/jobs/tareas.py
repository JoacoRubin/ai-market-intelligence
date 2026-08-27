"""QUÉ se ejecuta cuando se procesa un análisis.

Este archivo salió de `apps/api/main.py` al entrar el worker (ADR-012), y la
mudanza no es cosmética: mientras la lógica vivía dentro del módulo de la
app, un worker que quisiera invocarla tenía que importar FastAPI, los
handlers y el router entero para correr algo que no atiende una sola
petición HTTP.

Acá no se importa `rq` ni `redis` a propósito. Este módulo sabe ejecutar un
análisis; **no sabe dónde va a correr**. Esa decisión vive en
`apps/jobs/cola.py`, y esa separación es lo que permite testear la lógica con
un doble del modelo y sin infraestructura.
"""

from __future__ import annotations

from agent.graph import ejecutar as ejecutar_grafo
from agent.llm import ClienteLLM, crear_cliente
from agent.state import AnalysisState, Intencion, Periodo
from apps.api.schemas import Analisis, EstadoAnalisis
from apps.api.store import Almacen
from rag.build import cargar_indice


def estado_inicial(registro: Analisis) -> AnalysisState:
    """Construye el estado del grafo según cómo llegó la solicitud.

    Cuando vienen identificadores y rango, la interpretación ya está hecha: se
    precarga el estado y el router se saltea solo. Cuando viene lenguaje
    natural, el estado arranca vacío y el agente interpreta.
    """
    estado = AnalysisState(request_id=registro.id, consulta=registro.consulta)

    if registro.product_ids and registro.desde and registro.hasta:
        estado.intencion = Intencion.PRODUCT_PERFORMANCE
        estado.entidades = list(registro.product_ids)
        estado.periodo = Periodo(desde=registro.desde, hasta=registro.hasta)

    return estado


def procesar_analisis(
    analysis_id: str, cliente: ClienteLLM, almacen: Almacen
) -> None:
    """Ejecuta el grafo del agente y actualiza el recurso.

    Con el modelo real tarda cerca de dos minutos en esta máquina — que es
    exactamente el motivo por el que el POST responde 202 desde el principio
    y no hubo que cambiar el contrato al conectar el agente, ni ahora al
    mover el trabajo a un worker.

    El almacén se **inyecta** y no se importa como global: el worker corre en
    otro proceso y tiene que escribir en el almacén compartido, no en el
    diccionario en memoria de su propio proceso. Recibirlo por parámetro es
    lo que hace que esa diferencia sea imposible de olvidar.
    """
    registro = almacen.obtener(analysis_id)
    if registro is None:
        # El análisis pudo haberse borrado entre el encolado y el consumo —una
        # ventana que antes no existía, porque todo pasaba en el mismo
        # proceso—. Salir en silencio es correcto: no hay recurso que
        # actualizar y no es un error del sistema.
        return

    registro.estado = EstadoAnalisis.PROCESANDO
    almacen.guardar(registro)

    try:
        estado = ejecutar_grafo(
            estado_inicial(registro), cliente, indice=cargar_indice()
        )

        registro.informe = estado.informe
        registro.intencion = estado.intencion.value if estado.intencion else None
        registro.product_ids = estado.entidades
        if estado.periodo:
            registro.desde = estado.periodo.desde
            registro.hasta = estado.periodo.hasta
        registro.etapas = [p.nodo for p in estado.trace]
        registro.advertencias = list(estado.advertencias)
        registro.estado = EstadoAnalisis.COMPLETADO
    except Exception as e:  # el fallo viaja en el recurso, no revienta la API
        registro.estado = EstadoAnalisis.FALLIDO
        registro.error = f"{type(e).__name__}: {e}"
    almacen.guardar(registro)


def ejecutar_analisis(analysis_id: str) -> None:
    """Punto de entrada del job. Es lo que RQ invoca en el worker.

    Recibe **solo el id**, y esa firma es una decisión, no una simplificación:
    RQ serializa los argumentos con pickle para mandarlos por Redis, así que
    pasarle el cliente del modelo o el almacén significaría serializar
    conexiones abiertas. Cada proceso construye los suyos.

    Que el worker llame a `crear_cliente()` por su cuenta tiene además una
    consecuencia buena: respeta el `LLM_BACKEND` de SU entorno. El worker
    puede correr contra otro backend que la API sin que la API se entere,
    que es justamente lo que promete el puerto (ADR-007).
    """
    from apps.api.store import crear_almacen

    procesar_analisis(analysis_id, crear_cliente(), crear_almacen())
