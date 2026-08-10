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

from core.conclusiones import _alertas_de_devolucion, _conclusiones
from core.kpis import FUENTE, metricas_de_producto
from core.report import Fuente, PasoTrace, Report

MODELO_DETERMINISTICO = "sin-llm:analisis-deterministico-v1"


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
