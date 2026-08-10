"""Genera un informe PDF de ejemplo con datos representativos.

Sirve para dos cosas: ver el resultado del renderer sin tener que esperar a que
el agente exista, y tener un artefacto mostrable para la demo de portfolio.

Los datos son fijos y realistas a propósito — este archivo NO consulta la base
ni el LLM. Cuando el agente esté funcionando, el mismo renderer recibirá un
`Report` construido por el grafo en lugar de este de ejemplo.

Se ejecuta con:  .\\tasks.ps1 pdf
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from core.report import (
    Afirmacion,
    Anomalia,
    Fuente,
    MetricaProducto,
    PasoTrace,
    Prediccion,
    Report,
)
from core.report_pdf import render_pdf

DESTINO = Path(__file__).resolve().parent.parent / "docs" / "ejemplos" / "informe_ejemplo.pdf"

CONSULTADA = datetime(2026, 8, 9, 15, 41)


def informe_de_ejemplo() -> Report:
    return Report(
        request_id="req-8f1a2c",
        consulta=(
            "Compará Producto A y Producto B en los últimos 30 días, "
            "proyectá el próximo mes y detectá anomalías"
        ),
        generado_en=datetime(2026, 8, 9, 15, 42),
        modelo_llm="llama3.2:3b",
        fuentes=[
            Fuente(id="sql:product_metrics", tipo="sql",
                   referencia="dbo.order_items JOIN dbo.orders",
                   consultada_en=CONSULTADA),
            Fuente(id="ml:sales_v3", tipo="modelo_ml",
                   referencia="forecast_sales · sales_v3", consultada_en=CONSULTADA),
            Fuente(id="doc_112", tipo="documento",
                   referencia="Reporte interno de campañas Q1", seccion="§3.2",
                   consultada_en=CONSULTADA),
            Fuente(id="doc_087", tipo="documento",
                   referencia="Comunicación de proveedor · lote enero", seccion="§1.1",
                   consultada_en=CONSULTADA),
        ],
        resumen_ejecutivo=[
            Afirmacion(
                texto="El Producto A lidera en crecimiento con 18,4% respecto al "
                      "período previo",
                tipo="hecho", fuentes=["sql:product_metrics"]),
            Afirmacion(
                texto="El Producto B muestra deterioro de margen y una tasa de "
                      "devolución de 5,7%",
                tipo="hecho", fuentes=["sql:product_metrics"]),
            Afirmacion(
                texto="El pico de devoluciones del Producto B coincide con una "
                      "campaña de descuento sin control de stock por talle",
                tipo="hecho", fuentes=["doc_112", "doc_087"]),
        ],
        metricas=[
            MetricaProducto(product_id="P001", nombre="Producto A", unidades=1243,
                            revenue=87010.0, margen_pct=31.2, crecimiento_pct=18.4,
                            tasa_devolucion_pct=2.1, fuente="sql:product_metrics"),
            MetricaProducto(product_id="P002", nombre="Producto B", unidades=981,
                            revenue=92340.0, margen_pct=24.8, crecimiento_pct=-3.1,
                            tasa_devolucion_pct=5.7, fuente="sql:product_metrics"),
        ],
        predicciones=[
            Prediccion(product_id="P001", horizonte_dias=30, valor=1470.0,
                       mape_backtest=8.3, mape_baseline=14.1,
                       modelo_version="sales_v3"),
            Prediccion(product_id="P002", horizonte_dias=30, valor=910.0,
                       mape_backtest=11.9, mape_baseline=12.4,
                       modelo_version="sales_v3"),
        ],
        anomalias=[
            Anomalia(product_id="P002", fecha=date(2026, 1, 18),
                     tipo="pico_devoluciones", desvios=3.4,
                     descripcion="Pico de devoluciones muy por encima de la media "
                                 "móvil de 14 días",
                     evidencia=["doc_112", "doc_087"]),
        ],
        recomendaciones=[
            Afirmacion(texto="Revisar el control de stock por talle antes de la "
                             "próxima campaña de descuento en la línea B",
                       tipo="recomendacion"),
            Afirmacion(texto="Auditar el lote de enero del proveedor de la línea B "
                             "por desvíos de calidad",
                       tipo="recomendacion"),
        ],
        trace=[
            PasoTrace(nodo="router", duracion_ms=85),
            PasoTrace(nodo="sql_tool", duracion_ms=140, tool="product_metrics"),
            PasoTrace(nodo="rag_tool", duracion_ms=220, tool="search_documents"),
            PasoTrace(nodo="ml_tool", duracion_ms=55, tool="forecast_sales"),
            PasoTrace(nodo="synthesis", duracion_ms=113690),
        ],
        limitaciones=[
            "Los datos son sintéticos y no representan operaciones comerciales reales.",
            "El modelo de lenguaje corre en CPU local: la síntesis puede demorar minutos.",
        ],
    )


def main() -> int:
    informe = informe_de_ejemplo()
    ruta = render_pdf(informe, DESTINO)
    print(f"  PDF generado: {ruta}")
    print(f"  {ruta.stat().st_size:,} bytes")
    if informe.advertencias:
        print("\n  Advertencias automáticas incluidas en el informe:")
        for w in informe.advertencias:
            print(f"    - {w}")
    else:
        print("\n  Sin advertencias: ambas predicciones superan su baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
