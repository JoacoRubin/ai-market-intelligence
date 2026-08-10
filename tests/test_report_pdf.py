"""Tests del renderer PDF.

Un PDF se desprende del sistema que lo generó: se manda por mail, se imprime,
aparece en una reunión tres semanas después. Nadie que lo lea va a ver el
dashboard, el trace ni las advertencias de la pantalla.

Por eso estos tests no verifican solamente que el archivo se genere. Verifican
que el documento **se sostenga solo**: que diga cuándo se generó, con qué modelo,
de dónde salió cada dato, qué es predicción y qué es recomendación, y qué
limitaciones tiene — en todas las páginas, no solo en la primera.
"""

import re
from datetime import date, datetime

import pytest
from pypdf import PdfReader

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


@pytest.fixture(scope="module")
def informe() -> Report:
    return Report(
        request_id="req-8f1a2c",
        consulta="Compará Producto A y Producto B en los últimos 30 días",
        generado_en=datetime(2026, 8, 9, 15, 42),
        modelo_llm="llama3.2:3b",
        fuentes=[
            Fuente(id="sql:product_metrics", tipo="sql",
                   referencia="dbo.order_items / dbo.orders",
                   consultada_en=datetime(2026, 8, 9, 15, 41)),
            Fuente(id="ml:sales_v3", tipo="modelo_ml",
                   referencia="forecast_sales sales_v3",
                   consultada_en=datetime(2026, 8, 9, 15, 41)),
            Fuente(id="doc_112", tipo="documento",
                   referencia="Reporte interno de campañas Q1", seccion="§3.2",
                   consultada_en=datetime(2026, 8, 9, 15, 41)),
        ],
        resumen_ejecutivo=[
            Afirmacion(texto="El Producto A lidera en crecimiento con 18,4%",
                       tipo="hecho", fuentes=["sql:product_metrics"]),
            Afirmacion(texto="El Producto B muestra deterioro de margen",
                       tipo="hecho", fuentes=["sql:product_metrics"]),
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
        ],
        anomalias=[
            Anomalia(product_id="P002", fecha=date(2026, 1, 18),
                     tipo="pico_devoluciones", desvios=3.4,
                     descripcion="Pico de devoluciones fuera de patrón",
                     evidencia=["doc_112"]),
        ],
        recomendaciones=[
            Afirmacion(texto="Revisar el control de stock por talle en la línea B",
                       tipo="recomendacion"),
        ],
        trace=[PasoTrace(nodo="router", duracion_ms=85),
               PasoTrace(nodo="sql_tool", duracion_ms=140, tool="product_metrics"),
               PasoTrace(nodo="synthesis", duracion_ms=114000)],
        limitaciones=["Datos sintéticos. No representan operaciones reales."],
    )


@pytest.fixture(scope="module")
def pdf(informe: Report, tmp_path_factory) -> tuple[bytes, list[str]]:
    ruta = tmp_path_factory.mktemp("pdf") / "informe.pdf"
    render_pdf(informe, ruta)
    contenido = ruta.read_bytes()
    paginas = [p.extract_text() or "" for p in PdfReader(str(ruta)).pages]
    return contenido, paginas


# --- Que exista y sea un PDF de verdad ---------------------------------------

def test_genera_un_pdf_valido(pdf):
    contenido, paginas = pdf
    assert contenido.startswith(b"%PDF-"), "el archivo no es un PDF"
    assert len(contenido) > 2000
    assert len(paginas) >= 1


# --- Que se sostenga solo ----------------------------------------------------

def test_todas_las_paginas_identifican_el_origen(pdf):
    """El pie tiene que estar en TODAS las páginas.

    Si alguien imprime el informe y reparte hojas sueltas en una reunión, cada
    hoja debe seguir diciendo que la generó un sistema de IA y cuándo. Un
    disclaimer solo en la portada desaparece en cuanto el documento se parte.
    """
    _, paginas = pdf
    for i, texto in enumerate(paginas, 1):
        plano = texto.replace("\n", " ")
        assert "llama3.2:3b" in plano, f"la página {i} no dice qué modelo lo generó"
        assert re.search(r"09[/-]08[/-]2026|2026-08-09", plano), (
            f"la página {i} no lleva fecha de generación"
        )


def test_incluye_todas_las_secciones(pdf):
    _, paginas = pdf
    todo = " ".join(paginas)
    for seccion in ("Resumen", "Performance", "Predic", "Anomal",
                    "Recomendaciones", "Fuentes"):
        assert seccion.lower() in todo.lower(), f"falta la sección {seccion}"


def test_toda_fuente_declarada_aparece_en_el_documento(pdf, informe: Report):
    """Las fuentes no son un adorno del final: son lo que hace verificable el
    informe. Si una fuente se cita en el cuerpo pero no figura en la lista, el
    lector no puede rastrearla."""
    _, paginas = pdf
    todo = " ".join(paginas)
    for f in informe.fuentes:
        assert f.id in todo, f"la fuente {f.id} no aparece en el PDF"


def test_las_predicciones_estan_marcadas_como_tales(pdf):
    """Un número de forecast que se lee igual que un dato histórico es la forma
    más simple de que un informe engañe sin mentir."""
    _, paginas = pdf
    todo = " ".join(paginas).lower()
    assert "predicción" in todo or "prediccion" in todo
    assert "mape" in todo, "no se informa el error del modelo"
    assert "baseline" in todo, "no se informa contra qué baseline se comparó"


def test_las_recomendaciones_estan_separadas_de_los_hechos(pdf):
    _, paginas = pdf
    todo = " ".join(paginas)
    assert "Recomendaciones" in todo
    assert "no son hechos" in todo.lower() or "sugerid" in todo.lower(), (
        "el informe no aclara que las recomendaciones son juicios, no datos"
    )


def test_incluye_las_limitaciones(pdf):
    _, paginas = pdf
    todo = " ".join(paginas)
    assert "sintétic" in todo.lower(), "no advierte que los datos son sintéticos"


# --- Groundedness del renderer ----------------------------------------------

def test_ningun_numero_del_pdf_esta_inventado(pdf, informe: Report):
    """El renderer no puede introducir números que no estén en el modelo.

    Este test existe por lo que aprendimos auditando al LLM: un informe puede
    tener todos los números correctos y aun así estar mal. Acá cerramos la
    puerta del lado del software — si el renderer calcula o inventa algo por su
    cuenta, deja de haber una única fuente de verdad y el PDF empieza a decir
    cosas que la API no dice.
    """
    _, paginas = pdf

    permitidos = {
        1243, 981, 87010, 92340, 31.2, 24.8, 18.4, 3.1, 2.1, 5.7,
        1470, 8.3, 14.1, 30, 3.4, 85, 140, 114000, 112, 3.2, 2026, 1, 18, 8, 9,
        15, 42, 41, 114, 2, 3, 0, 5, 4, 7, 41.0,
    }
    # Duraciones derivadas que el renderer sí puede computar del propio modelo.
    permitidos.add(informe.duracion_total_ms)
    permitidos.add(round(informe.duracion_total_ms / 1000))

    sospechosos = []
    for texto in paginas:
        for token in re.findall(r"\d+(?:[.,]\d+)?", texto):
            crudo = token.replace(".", "").replace(",", ".")
            try:
                valor = float(crudo)
            except ValueError:
                continue
            alterno = float(token.replace(",", "."))
            if valor not in permitidos and alterno not in permitidos:
                sospechosos.append(token)

    assert not sospechosos, (
        f"el PDF contiene números que no salen del modelo: {sorted(set(sospechosos))}"
    )
