"""Ejecución de análisis fuera del proceso que atiende HTTP.

Tres piezas, cada una con una responsabilidad:

  - `tareas`   — QUÉ se ejecuta. La lógica del análisis, sin saber si la
                 invocó un `BackgroundTask` o un worker de RQ.
  - `cola`     — DÓNDE se ejecuta. El despacho, que elige entre el proceso
                 mismo y la cola de Redis según `JOBS_BACKEND`.
  - `worker`   — el entrypoint del proceso que consume la cola.

Esa separación es el punto del módulo: `tareas` no importa `rq` ni `redis`,
así que la lógica del análisis se testea sin infraestructura y sin haber
elegido todavía dónde va a correr.
"""
