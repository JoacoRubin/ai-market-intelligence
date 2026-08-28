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

from agent.llm import crear_cliente
from application.analisis import estado_inicial, procesar_analisis
from apps.api.store import crear_almacen

__all__ = ["ejecutar_analisis", "estado_inicial", "procesar_analisis"]


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
    procesar_analisis(
        analysis_id,
        crear_cliente(),
        crear_almacen(),
        propagar_error=True,
    )
