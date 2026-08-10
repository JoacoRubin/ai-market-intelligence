"""Construcción del análisis.

En la Fase 1 el informe se arma de forma **completamente determinística**: los
KPIs salen de SQL y las conclusiones se derivan comparando esos números. No
interviene ningún modelo de lenguaje.

Eso no es una limitación temporal que haya que disculpar: es la base sobre la
que el agente va a trabajar. Cuando en la Fase 2 entre LangGraph, va a AGREGAR
interpretación y evidencia documental sobre un informe que ya es correcto. Si
el LLM falla, se degrada a esto — que sigue siendo un informe válido con
números verificados.

Un sistema que sin el modelo no produce nada es un sistema que depende del
modelo para tener razón. Este no.
"""

from __future__ import annotations

from datetime import datetime

from core.kpis import FUENTE, metricas_de_producto
from core.report import Afirmacion, Fuente, MetricaProducto, PasoTrace, Report

MODELO_DETERMINISTICO = "sin-llm:analisis-deterministico-v1"


def _miles(valor: float) -> str:
    """Formato español de miles: 1500 -> '1.500'.

    Existe como función porque hacer `f"{n:,}".replace(",", ".")` sobre una
    frase ya armada también reemplaza las comas gramaticales. Ese bug produjo
    "lidera con 242. frente a 0" en el informe.
    """
    return f"{valor:,.0f}".replace(",", ".")


def _pct(valor: float) -> str:
    """Formato español de porcentaje: 31.2 -> '31,2%'."""
    return f"{valor:.1f}".replace(".", ",") + "%"


def _mejor_por(metricas: list[MetricaProducto], campo: str) -> MetricaProducto | None:
    con_dato = [m for m in metricas if getattr(m, campo) is not None]
    return max(con_dato, key=lambda m: getattr(m, campo)) if con_dato else None


def _conclusiones(metricas: list[MetricaProducto]) -> list[Afirmacion]:
    """Deriva conclusiones comparando los KPIs.

    Cada frase es un HECHO con su fuente, porque cada una se apoya en números
    que salieron de una consulta. Nada acá es interpretación: es aritmética
    redactada en castellano.
    """
    def hecho(texto: str) -> Afirmacion:
        return Afirmacion(texto=texto, tipo="hecho", fuentes=[FUENTE])

    if len(metricas) < 2:
        m = metricas[0]
        return [hecho(
            f"{m.nombre} ({m.product_id}) vendió {_miles(m.unidades)} unidades "
            f"por un revenue de USD {_miles(m.revenue)}"
        )]

    conclusiones: list[Afirmacion] = []

    lider_unidades = _mejor_por(metricas, "unidades")
    if lider_unidades:
        resto = [m for m in metricas if m.product_id != lider_unidades.product_id]
        segundo = max(r.unidades for r in resto)
        conclusiones.append(hecho(
            f"{lider_unidades.nombre} ({lider_unidades.product_id}) lidera en "
            f"unidades con {_miles(lider_unidades.unidades)}, frente a "
            f"{_miles(segundo)} del siguiente"
        ))

    lider_revenue = _mejor_por(metricas, "revenue")
    if lider_revenue and lider_unidades and (
        lider_revenue.product_id != lider_unidades.product_id
    ):
        # Este caso merece señalarse: más unidades no siempre es más ingreso.
        conclusiones.append(hecho(
            f"{lider_revenue.nombre} ({lider_revenue.product_id}) genera más "
            f"revenue pese a vender menos unidades: el líder en volumen no "
            f"es el líder en facturación"
        ))

    lider_margen = _mejor_por(metricas, "margen_pct")
    if lider_margen:
        conclusiones.append(hecho(
            f"{lider_margen.nombre} ({lider_margen.product_id}) tiene el mejor "
            f"margen del grupo: {_pct(lider_margen.margen_pct)}"
        ))

    for m in metricas:
        if m.crecimiento_pct is not None and m.crecimiento_pct < 0:
            conclusiones.append(hecho(
                f"{m.nombre} ({m.product_id}) cae "
                f"{_pct(abs(m.crecimiento_pct))} respecto al período previo"
            ))

    return conclusiones


def _alertas_de_devolucion(metricas: list[MetricaProducto]) -> list[str]:
    """Señala tasas de devolución que se despegan del resto del grupo.

    No es detección de anomalías todavía —eso llega en la Fase 4 con un modelo—
    pero un producto que devuelve el doble que sus pares merece una advertencia
    aunque no haya ML de por medio.
    """
    tasas = [m.tasa_devolucion_pct for m in metricas
             if m.tasa_devolucion_pct is not None]
    if len(tasas) < 2:
        return []
    promedio = sum(tasas) / len(tasas)
    if promedio == 0:
        return []
    return [
        f"{m.nombre} ({m.product_id}) tiene una tasa de devolución de "
        f"{_pct(m.tasa_devolucion_pct)}, más del doble del promedio del grupo "
        f"({_pct(promedio)})"
        for m in metricas
        if m.tasa_devolucion_pct is not None
        and m.tasa_devolucion_pct > promedio * 2
    ]


def construir_informe(
    request_id: str, consulta: str, product_ids: list[str], desde, hasta
) -> Report:
    """Arma el informe completo consultando los KPIs de cada producto."""
    inicio = datetime.now()

    metricas = [metricas_de_producto(pid, desde, hasta) for pid in product_ids]
    duracion_sql = int((datetime.now() - inicio).total_seconds() * 1000)

    return Report(
        request_id=request_id,
        consulta=consulta,
        generado_en=datetime.now(),
        modelo_llm=MODELO_DETERMINISTICO,
        fuentes=[Fuente(
            id=FUENTE, tipo="sql",
            referencia="dbo.order_items JOIN dbo.orders JOIN dbo.products",
            consultada_en=inicio,
        )],
        resumen_ejecutivo=_conclusiones(metricas),
        metricas=metricas,
        advertencias=_alertas_de_devolucion(metricas),
        trace=[PasoTrace(nodo="sql_tool", duracion_ms=duracion_sql,
                         tool="product_metrics")],
        limitaciones=[
            "Los datos son sintéticos y no representan operaciones comerciales reales.",
            "Análisis determinístico sin modelo de lenguaje: no incluye evidencia "
            "documental ni contexto de mercado.",
        ],
    )
