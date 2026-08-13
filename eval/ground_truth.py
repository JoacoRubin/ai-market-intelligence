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


def casos_de_evaluacion(eventos: list[EventoSembrado]) -> list[EventoSembrado]:
    """Reduce los eventos a casos con una consulta distinta cada uno.

    `consulta_para` redacta con producto y **mes**, así que dos eventos del
    mismo producto y mes producen el mismo texto. Eso es deliberado —es lo que
    impide que el enunciado filtre la respuesta— pero significa que no pueden
    contarse como dos casos: el agente recibiría dos veces la misma pregunta y
    el eval anotaría dos resultados sobre una sola observación.

    La corrida del 2026-08-12 pagó ese precio. Los eventos 3 y 4 eran
    `pico_ventas` de P033 el 09 y el 11 de junio: misma consulta, mismas
    magnitudes en el informe, y resultados opuestos en
    `usa_la_evidencia_documental`. Seis corridas sobre cinco preguntas, con la
    repetida pesando doble.

    **Un grupo con anomalías de distinto tipo se descarta entero.** Ahí no
    alcanza con quedarse con una: la consulta no puede distinguirlas, y medir
    contra la que el `ORDER BY` puso primero sería elegir el oráculo por
    casualidad. Un caso que no se puede juzgar no se juzga.
    """
    grupos: dict[tuple[str, str], list[EventoSembrado]] = {}
    for evento in eventos:
        clave = (evento.product_id, evento.fecha.strftime("%Y-%m"))
        grupos.setdefault(clave, []).append(evento)

    casos = [
        min(grupo, key=lambda e: (e.fecha, e.tipo))
        for grupo in grupos.values()
        if len({e.tipo for e in grupo}) == 1
    ]
    # El mismo motivo que el ORDER BY de `leer_eventos`: dos corridas tienen que
    # recorrer los mismos casos en la misma secuencia.
    return sorted(casos, key=lambda e: (e.fecha, e.product_id))


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


def consulta_con_proyeccion(evento: EventoSembrado) -> str:
    """La misma pregunta, pidiendo además una proyección.

    Existe porque `no_invierte_el_sentido_del_error` estuvo cuatro corridas
    marcada NUNCA APLICÓ, y la causa no era el agente: era el enunciado. El
    planner solo planifica `forecast_sales` cuando la consulta pide una
    proyección (`planner.PIDE_PROYECCION`), y `consulta_para` no lo pide. El
    agente hacía lo correcto —no entrena un modelo con backtesting para quien
    solo pidió KPIs— y la métrica juzgaba cómo el informe describe un MAPE que
    nunca iba a existir.

    Se mantiene la disciplina del enunciado: no nombra el evento, no adelanta
    su magnitud y no dice qué se va a encontrar. Lo único que agrega es el
    pedido de proyección.
    """
    mes = evento.fecha.strftime("%Y-%m")
    return (
        f"Analizá el desempeño de {evento.product_id} durante {mes}: "
        f"unidades, revenue, margen y devoluciones. Si ves algo fuera de lo "
        f"normal, explicá a qué puede deberse, y proyectá la demanda de los "
        f"próximos 30 días."
    )
