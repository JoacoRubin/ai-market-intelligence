"""Lectura de los eventos sembrados: la lista de respuestas del examen.

Este módulo usa `conectar_admin` a propósito, y es la única parte del proyecto
fuera del ETL que lo hace. El usuario del agente tiene `DENY SELECT` explícito
sobre `dbo.ground_truth` (ver infra/sql/02_readonly_user.sql):

    "Darle acceso sería hacer trampa: el sistema debe DETECTAR las anomalías,
    no leer la lista de respuestas."

Que el evaluador entre por otra puerta no es un rodeo del guardrail: es lo que
lo vuelve una evaluación. Si el agente pudiera leer esta tabla, medir su
capacidad de detección no significaría nada.
"""

from __future__ import annotations

from core.db import conectar_admin
from eval.metricas import EventoSembrado

# El agente investiga una anomalía cuando se la puede describir en una consulta.
# Los tres tipos que siembra el generador son los tres que se evalúan.
TIPOS = ("pico_ventas", "caida_ventas", "pico_devoluciones")


def leer_eventos(tipos: tuple[str, ...] = TIPOS) -> list[EventoSembrado]:
    """Devuelve los eventos sembrados, ordenados por fecha.

    El orden es estable para que dos corridas del eval recorran los mismos casos
    en la misma secuencia. Un eval cuyo orden cambia entre corridas produce
    diferencias que parecen del modelo y son del `ORDER BY` que falta.
    """
    marcadores = ", ".join("?" for _ in tipos)
    con = conectar_admin()
    try:
        filas = con.cursor().execute(
            f"""SELECT tipo, product_id, fecha, magnitud, descripcion
                FROM dbo.ground_truth
                WHERE tipo IN ({marcadores})
                ORDER BY fecha, product_id""",
            tuple(tipos),
        ).fetchall()
    finally:
        con.close()

    return [
        EventoSembrado(
            tipo=f[0],
            product_id=f[1],
            fecha=f[2],
            magnitud=float(f[3]) if f[3] is not None else None,
            descripcion=f[4],
        )
        for f in filas
    ]


def consulta_para(evento: EventoSembrado) -> str:
    """Redacta la consulta en castellano con la que se interroga al agente.

    **No nombra el evento.** Se pregunta por el producto y el período, y el
    agente tiene que llegar solo a la anomalía. Preguntar "¿por qué subieron las
    devoluciones de P010 el 14 de febrero?" sería filtrarle la respuesta en el
    enunciado y medir su redacción en vez de su análisis.
    """
    mes = evento.fecha.strftime("%Y-%m")
    return (
        f"Analizá el desempeño de {evento.product_id} durante {mes}: "
        f"unidades, revenue, margen y devoluciones. Si ves algo fuera de lo "
        f"normal, explicá a qué puede deberse."
    )
